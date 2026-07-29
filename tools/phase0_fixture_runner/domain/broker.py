from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .contract import (
    DomainContractError,
    DomainRegistration,
    IsolatedDomainContext,
    canonical_json_bytes,
    normalize_json_value,
    require_bool,
    require_exact_fields,
    require_mapping,
    require_nonnegative_int,
    require_string,
)

_BASE_FIXTURES = (
    "GB-01",
    "GB-03",
    "GB-04",
    "GB-05",
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "GB-01": "broker.gb01.atomic_cas_commit",
    "GB-03": "broker.gb03.idempotency_sequence",
    "GB-04": "broker.gb04.recovery_without_replay",
    "GB-05": "broker.gb05.immutable_terminal_receipt",
}

_FAULT_POINTS = frozenset(
    {
        "NONE",
        "BEFORE_COMMIT",
        "AFTER_COMMIT",
    }
)
_TERMINAL_KINDS = frozenset(
    {
        "EFFECT_SUCCEEDED",
        "EFFECT_FAILED",
    }
)
_GB03_DISPOSITIONS = frozenset(
    {
        "MUTATED",
        "IDEMPOTENCY_HIT",
        "IDEMPOTENCY_PAYLOAD_MISMATCH",
    }
)
_GB04_DISPOSITIONS = frozenset(
    {
        "PENDING_CONFIRMATION_REQUIRED",
        "BLINDLY_REDISPATCHED",
    }
)
_GB05_DISPOSITIONS = frozenset(
    {
        "COMPETING_RECEIPT_REJECTED",
        "OVERWRITTEN",
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


def _require_terminal_kind(
    value: Any,
    path: str,
) -> str:
    kind = require_string(value, path)
    if kind not in _TERMINAL_KINDS:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} unsupported={kind}",
        )
    return kind


def _validated_payload(
    value: Any,
    path: str,
) -> dict[str, Any]:
    payload = require_mapping(value, path)
    normalized = normalize_json_value(payload)
    if not isinstance(normalized, dict):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} must be an object",
        )
    return normalized


def _validate_submission(
    value: Any,
    path: str,
) -> dict[str, Any]:
    submission = require_mapping(value, path)
    require_exact_fields(
        submission,
        {
            "client_id",
            "command_type",
            "idempotency_key",
            "payload",
        },
        path=path,
    )
    return {
        "client_id": require_string(
            submission["client_id"],
            f"{path}.client_id",
        ),
        "command_type": require_string(
            submission["command_type"],
            f"{path}.command_type",
        ),
        "idempotency_key": require_string(
            submission["idempotency_key"],
            f"{path}.idempotency_key",
        ),
        "payload": _validated_payload(
            submission["payload"],
            f"{path}.payload",
        ),
    }


def validate_broker_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one closed broker input schema."""

    base = _base_fixture_id(fixture_id)
    inputs = require_mapping(raw_inputs, "inputs")

    if base == "GB-01":
        require_exact_fields(
            inputs,
            {
                "target_revision",
                "pending_receipt",
                "outbox_row",
                "fault_point",
            },
            path="inputs",
        )
        fault_point = require_string(
            inputs["fault_point"],
            "inputs.fault_point",
        )
        if fault_point not in _FAULT_POINTS:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "inputs.fault_point "
                    f"unsupported={fault_point}"
                ),
            )

        return {
            "target_revision": require_nonnegative_int(
                inputs["target_revision"],
                "inputs.target_revision",
            ),
            "pending_receipt": require_string(
                inputs["pending_receipt"],
                "inputs.pending_receipt",
            ),
            "outbox_row": require_string(
                inputs["outbox_row"],
                "inputs.outbox_row",
            ),
            "fault_point": fault_point,
        }

    if base == "GB-03":
        require_exact_fields(
            inputs,
            {"submissions"},
            path="inputs",
        )
        submissions = inputs["submissions"]
        if (
            not isinstance(submissions, list)
            or len(submissions) < 3
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "inputs.submissions must contain "
                    "at least three submissions"
                ),
            )

        return {
            "submissions": [
                _validate_submission(
                    submission,
                    f"inputs.submissions[{index}]",
                )
                for index, submission in enumerate(
                    submissions
                )
            ]
        }

    if base == "GB-04":
        require_exact_fields(
            inputs,
            {
                "transition_id",
                "outbox_id",
                "outbox_state",
            },
            path="inputs",
        )
        outbox_state = require_string(
            inputs["outbox_state"],
            "inputs.outbox_state",
        )
        if outbox_state != "PENDING_UNCOMMITTED":
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "inputs.outbox_state must equal "
                    "PENDING_UNCOMMITTED"
                ),
            )

        return {
            "transition_id": require_string(
                inputs["transition_id"],
                "inputs.transition_id",
            ),
            "outbox_id": require_string(
                inputs["outbox_id"],
                "inputs.outbox_id",
            ),
            "outbox_state": outbox_state,
        }

    require_exact_fields(
        inputs,
        {
            "request_id",
            "outbox_id",
            "attempt_id",
            "first_owner_id",
            "first_terminal_kind",
            "second_owner_id",
            "second_terminal_kind",
        },
        path="inputs",
    )

    first_owner_id = require_string(
        inputs["first_owner_id"],
        "inputs.first_owner_id",
    )
    first_terminal_kind = _require_terminal_kind(
        inputs["first_terminal_kind"],
        "inputs.first_terminal_kind",
    )
    second_owner_id = require_string(
        inputs["second_owner_id"],
        "inputs.second_owner_id",
    )
    second_terminal_kind = _require_terminal_kind(
        inputs["second_terminal_kind"],
        "inputs.second_terminal_kind",
    )
    if (
        first_owner_id == second_owner_id
        and first_terminal_kind == second_terminal_kind
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "GB-05 second submission must differ "
                "by owner or terminal kind"
            ),
        )

    return {
        "request_id": require_string(
            inputs["request_id"],
            "inputs.request_id",
        ),
        "outbox_id": require_string(
            inputs["outbox_id"],
            "inputs.outbox_id",
        ),
        "attempt_id": require_string(
            inputs["attempt_id"],
            "inputs.attempt_id",
        ),
        "first_owner_id": first_owner_id,
        "first_terminal_kind": first_terminal_kind,
        "second_owner_id": second_owner_id,
        "second_terminal_kind": second_terminal_kind,
    }


def _validate_optional_receipt(
    value: Any,
    path: str,
) -> str | None:
    if value is None:
        return None
    return require_string(value, path)


def _validate_gb03_rows(
    value: Any,
    path: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            f"{path} must be a non-empty array",
        )

    rows: list[dict[str, Any]] = []
    for index, raw_row in enumerate(value):
        row_path = f"{path}[{index}]"
        row = require_mapping(raw_row, row_path)
        require_exact_fields(
            row,
            {
                "submission_index",
                "disposition",
                "receipt",
            },
            path=row_path,
        )
        disposition = require_string(
            row["disposition"],
            f"{row_path}.disposition",
        )
        if disposition not in _GB03_DISPOSITIONS:
            raise DomainContractError(
                "DOMAIN_OUTPUT_INVALID",
                (
                    f"{row_path}.disposition "
                    f"unsupported={disposition}"
                ),
            )

        rows.append(
            {
                "submission_index": require_nonnegative_int(
                    row["submission_index"],
                    f"{row_path}.submission_index",
                ),
                "disposition": disposition,
                "receipt": _validate_optional_receipt(
                    row["receipt"],
                    f"{row_path}.receipt",
                ),
            }
        )

    if [
        row["submission_index"]
        for row in rows
    ] != list(range(len(rows))):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                f"{path}.submission_index values "
                "must be contiguous from zero"
            ),
        )

    return rows


def _validate_stored_receipt(
    value: Any,
    path: str,
) -> dict[str, str]:
    receipt = require_mapping(value, path)
    require_exact_fields(
        receipt,
        {
            "request_id",
            "outbox_id",
            "attempt_id",
            "owner_id",
            "terminal_result",
        },
        path=path,
    )

    terminal_result = require_string(
        receipt["terminal_result"],
        f"{path}.terminal_result",
    )
    if terminal_result not in _TERMINAL_KINDS:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                f"{path}.terminal_result "
                f"unsupported={terminal_result}"
            ),
        )

    return {
        "request_id": require_string(
            receipt["request_id"],
            f"{path}.request_id",
        ),
        "outbox_id": require_string(
            receipt["outbox_id"],
            f"{path}.outbox_id",
        ),
        "attempt_id": require_string(
            receipt["attempt_id"],
            f"{path}.attempt_id",
        ),
        "owner_id": require_string(
            receipt["owner_id"],
            f"{path}.owner_id",
        ),
        "terminal_result": terminal_result,
    }


def validate_broker_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate oracle and subject broker output."""

    base = _base_fixture_id(fixture_id)
    output = require_mapping(raw_output, "output")

    if base == "GB-01":
        require_exact_fields(
            output,
            {
                "revision_row_present",
                "pending_receipt_present",
                "outbox_row_present",
                "both_or_neither",
            },
            path="output",
        )
        return {
            "revision_row_present": require_bool(
                output["revision_row_present"],
                "output.revision_row_present",
            ),
            "pending_receipt_present": require_bool(
                output["pending_receipt_present"],
                "output.pending_receipt_present",
            ),
            "outbox_row_present": require_bool(
                output["outbox_row_present"],
                "output.outbox_row_present",
            ),
            "both_or_neither": require_bool(
                output["both_or_neither"],
                "output.both_or_neither",
            ),
        }

    if base == "GB-03":
        require_exact_fields(
            output,
            {
                "submissions",
                "mutation_count",
            },
            path="output",
        )
        return {
            "submissions": _validate_gb03_rows(
                output["submissions"],
                "output.submissions",
            ),
            "mutation_count": require_nonnegative_int(
                output["mutation_count"],
                "output.mutation_count",
            ),
        }

    if base == "GB-04":
        require_exact_fields(
            output,
            {
                "transition_applies",
                "blind_replays",
                "outbox_disposition",
            },
            path="output",
        )
        disposition = require_string(
            output["outbox_disposition"],
            "output.outbox_disposition",
        )
        if disposition not in _GB04_DISPOSITIONS:
            raise DomainContractError(
                "DOMAIN_OUTPUT_INVALID",
                (
                    "output.outbox_disposition "
                    f"unsupported={disposition}"
                ),
            )

        return {
            "transition_applies": require_nonnegative_int(
                output["transition_applies"],
                "output.transition_applies",
            ),
            "blind_replays": require_nonnegative_int(
                output["blind_replays"],
                "output.blind_replays",
            ),
            "outbox_disposition": disposition,
        }

    require_exact_fields(
        output,
        {
            "stored_receipt",
            "second_disposition",
        },
        path="output",
    )
    disposition = require_string(
        output["second_disposition"],
        "output.second_disposition",
    )
    if disposition not in _GB05_DISPOSITIONS:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "output.second_disposition "
                f"unsupported={disposition}"
            ),
        )

    return {
        "stored_receipt": _validate_stored_receipt(
            output["stored_receipt"],
            "output.stored_receipt",
        ),
        "second_disposition": disposition,
    }


class BrokerOracle:
    """Pure GB oracle; it never opens or inspects SQLite."""

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

        if self._base == "GB-01":
            return self._gb01(raw_inputs)
        if self._base == "GB-03":
            return self._gb03(raw_inputs)
        if self._base == "GB-04":
            return self._gb04(raw_inputs)
        return self._gb05(raw_inputs)

    def _gb01(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        committed = (
            inputs["fault_point"] != "BEFORE_COMMIT"
        )
        return {
            "revision_row_present": committed,
            "pending_receipt_present": committed,
            "outbox_row_present": committed,
            "both_or_neither": True,
        }

    def _gb03(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        bindings: dict[
            tuple[str, str, str],
            tuple[str, str],
        ] = {}
        mutation_count = 0
        rows: list[dict[str, Any]] = []

        for index, submission in enumerate(
            inputs["submissions"]
        ):
            identity = (
                submission["client_id"],
                submission["command_type"],
                submission["idempotency_key"],
            )
            payload_digest = hashlib.sha256(
                canonical_json_bytes(
                    submission["payload"]
                )
            ).hexdigest()
            previous = bindings.get(identity)

            if previous is None:
                receipt = (
                    "receipt-"
                    + hashlib.sha256(
                        canonical_json_bytes(
                            {
                                "client_id": identity[0],
                                "command_type": identity[1],
                                "idempotency_key": identity[2],
                                "payload": submission["payload"],
                            }
                        )
                    ).hexdigest()
                )
                bindings[identity] = (
                    payload_digest,
                    receipt,
                )
                mutation_count += 1
                disposition = "MUTATED"
            elif previous[0] == payload_digest:
                receipt = previous[1]
                disposition = "IDEMPOTENCY_HIT"
            else:
                receipt = None
                disposition = (
                    "IDEMPOTENCY_PAYLOAD_MISMATCH"
                )

            rows.append(
                {
                    "submission_index": index,
                    "disposition": disposition,
                    "receipt": receipt,
                }
            )

        return {
            "submissions": rows,
            "mutation_count": mutation_count,
        }

    def _gb04(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        del inputs
        return {
            "transition_applies": 1,
            "blind_replays": 0,
            "outbox_disposition": (
                "PENDING_CONFIRMATION_REQUIRED"
            ),
        }

    def _gb05(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "stored_receipt": {
                "request_id": inputs["request_id"],
                "outbox_id": inputs["outbox_id"],
                "attempt_id": inputs["attempt_id"],
                "owner_id": inputs["first_owner_id"],
                "terminal_result": (
                    inputs["first_terminal_kind"]
                ),
            },
            "second_disposition": (
                "COMPETING_RECEIPT_REJECTED"
            ),
        }


class BrokerSubjectAdapter:
    """Reference GB adapter; GB-01 uses only fixture-root SQLite."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = f"broker.{label}.reference"
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
                (
                    f"adapter_id={self.adapter_id};"
                    f"fixture_id={fixture_id}"
                ),
            )

        if self._base == "GB-01":
            return self._gb01(
                raw_inputs,
                context,
                commit_before_fault=False,
            )
        if self._base == "GB-03":
            return self._gb03(
                raw_inputs,
                mutate_identical_repeat=False,
            )
        if self._base == "GB-04":
            return self._gb04(
                raw_inputs,
                blindly_redispatch=False,
            )
        return self._gb05(
            raw_inputs,
            overwrite_second_receipt=False,
        )

    def _database_path(
        self,
        context: IsolatedDomainContext,
    ) -> Path:
        if not context.root.is_dir():
            raise DomainContractError(
                "DOMAIN_ROOT_INVALID",
                "GB-01 fixture root does not exist",
            )

        path = context.root / "gb01-broker.sqlite"
        if path.exists():
            raise DomainContractError(
                "DOMAIN_ROOT_INVALID",
                "GB-01 SQLite database already exists",
            )
        return path

    def _gb01(
        self,
        inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
        *,
        commit_before_fault: bool,
    ) -> dict[str, Any]:
        database_path = self._database_path(context)
        connection = sqlite3.connect(
            str(database_path),
            isolation_level=None,
        )
        try:
            connection.execute(
                """
                CREATE TABLE revision_state (
                    target_revision INTEGER NOT NULL,
                    pending_receipt TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE outbox (
                    outbox_row TEXT NOT NULL
                )
                """
            )
            connection.execute("BEGIN")
            connection.execute(
                """
                INSERT INTO revision_state (
                    target_revision,
                    pending_receipt
                ) VALUES (?, ?)
                """,
                (
                    inputs["target_revision"],
                    inputs["pending_receipt"],
                ),
            )
            connection.execute(
                "INSERT INTO outbox (outbox_row) VALUES (?)",
                (inputs["outbox_row"],),
            )

            if inputs["fault_point"] == "BEFORE_COMMIT":
                if commit_before_fault:
                    connection.execute("COMMIT")
                else:
                    connection.execute("ROLLBACK")
            else:
                connection.execute("COMMIT")
        finally:
            connection.close()

        return self._read_gb01_state(
            database_path,
            inputs,
        )

    def _read_gb01_state(
        self,
        database_path: Path,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        connection = sqlite3.connect(str(database_path))
        try:
            revision_row = connection.execute(
                """
                SELECT target_revision, pending_receipt
                FROM revision_state
                LIMIT 1
                """
            ).fetchone()
            outbox_row = connection.execute(
                """
                SELECT outbox_row
                FROM outbox
                LIMIT 1
                """
            ).fetchone()
        finally:
            connection.close()

        revision_present = (
            revision_row is not None
            and revision_row[0] == inputs["target_revision"]
        )
        receipt_present = (
            revision_row is not None
            and revision_row[1] == inputs["pending_receipt"]
        )
        outbox_present = (
            outbox_row is not None
            and outbox_row[0] == inputs["outbox_row"]
        )

        return {
            "revision_row_present": revision_present,
            "pending_receipt_present": receipt_present,
            "outbox_row_present": outbox_present,
            "both_or_neither": (
                revision_present
                == receipt_present
                == outbox_present
            ),
        }

    def _gb03(
        self,
        inputs: Mapping[str, Any],
        *,
        mutate_identical_repeat: bool,
    ) -> dict[str, Any]:
        bindings: dict[
            tuple[str, str, str],
            tuple[str, str],
        ] = {}
        mutation_count = 0
        rows: list[dict[str, Any]] = []

        for index, submission in enumerate(
            inputs["submissions"]
        ):
            identity = (
                submission["client_id"],
                submission["command_type"],
                submission["idempotency_key"],
            )
            payload_bytes = canonical_json_bytes(
                submission["payload"]
            )
            payload_digest = hashlib.sha256(
                payload_bytes
            ).hexdigest()
            previous = bindings.get(identity)

            if previous is None:
                receipt = (
                    "receipt-"
                    + hashlib.sha256(
                        canonical_json_bytes(
                            {
                                "payload": submission["payload"],
                                "idempotency_key": identity[2],
                                "command_type": identity[1],
                                "client_id": identity[0],
                            }
                        )
                    ).hexdigest()
                )
                bindings[identity] = (
                    payload_digest,
                    receipt,
                )
                mutation_count += 1
                disposition = "MUTATED"
            elif previous[0] != payload_digest:
                receipt = None
                disposition = (
                    "IDEMPOTENCY_PAYLOAD_MISMATCH"
                )
            elif mutate_identical_repeat:
                receipt = previous[1]
                mutation_count += 1
                disposition = "MUTATED"
            else:
                receipt = previous[1]
                disposition = "IDEMPOTENCY_HIT"

            rows.append(
                {
                    "submission_index": index,
                    "disposition": disposition,
                    "receipt": receipt,
                }
            )

        return {
            "submissions": rows,
            "mutation_count": mutation_count,
        }

    def _gb04(
        self,
        inputs: Mapping[str, Any],
        *,
        blindly_redispatch: bool,
    ) -> dict[str, Any]:
        del inputs
        return {
            "transition_applies": 1,
            "blind_replays": (
                1 if blindly_redispatch else 0
            ),
            "outbox_disposition": (
                "BLINDLY_REDISPATCHED"
                if blindly_redispatch
                else "PENDING_CONFIRMATION_REQUIRED"
            ),
        }

    def _gb05(
        self,
        inputs: Mapping[str, Any],
        *,
        overwrite_second_receipt: bool,
    ) -> dict[str, Any]:
        stored_receipt = {
            "request_id": inputs["request_id"],
            "outbox_id": inputs["outbox_id"],
            "attempt_id": inputs["attempt_id"],
            "owner_id": inputs["first_owner_id"],
            "terminal_result": (
                inputs["first_terminal_kind"]
            ),
        }

        if overwrite_second_receipt:
            stored_receipt = {
                "request_id": inputs["request_id"],
                "outbox_id": inputs["outbox_id"],
                "attempt_id": inputs["attempt_id"],
                "owner_id": inputs["second_owner_id"],
                "terminal_result": (
                    inputs["second_terminal_kind"]
                ),
            }
            disposition = "OVERWRITTEN"
        else:
            disposition = (
                "COMPETING_RECEIPT_REJECTED"
            )

        return {
            "stored_receipt": stored_receipt,
            "second_disposition": disposition,
        }


class FaultInjectedBrokerAdapter(
    BrokerSubjectAdapter
):
    """Deliberately incorrect GB adapters for negative fixtures."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = f"broker.{label}.fault_injected"
        self.fixture_ids = frozenset(
            {f"{base_fixture_id}{_NEGATIVE_SUFFIX}"}
        )

    def execute(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]:
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

        if self._base == "GB-01":
            return self._gb01(
                raw_inputs,
                context,
                commit_before_fault=True,
            )
        if self._base == "GB-03":
            return self._gb03(
                raw_inputs,
                mutate_identical_repeat=True,
            )
        if self._base == "GB-04":
            return self._gb04(
                raw_inputs,
                blindly_redispatch=True,
            )
        return self._gb05(
            raw_inputs,
            overwrite_second_receipt=True,
        )


def broker_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return immutable built-in GB registry rows."""

    registrations: list[DomainRegistration] = []

    for base_fixture_id in _BASE_FIXTURES:
        oracle = BrokerOracle(base_fixture_id)
        positive_adapter = BrokerSubjectAdapter(
            base_fixture_id
        )
        negative_adapter = FaultInjectedBrokerAdapter(
            base_fixture_id
        )

        registrations.append(
            DomainRegistration(
                fixture_id=base_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=positive_adapter,
                input_validator=validate_broker_inputs,
                output_validator=validate_broker_output,
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
                input_validator=validate_broker_inputs,
                output_validator=validate_broker_output,
            )
        )

    return tuple(registrations)