from __future__ import annotations

import sqlite3
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
    f"AC-04-{index:02d}"
    for index in range(1, 7)
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "AC-04-01": (
        "authority_fence.ac0401."
        "pre_marker_write_unchanged_epoch"
    ),
    "AC-04-02": (
        "authority_fence.ac0402."
        "stale_lease_epoch_fenced"
    ),
    "AC-04-03": (
        "authority_fence.ac0403."
        "mandatory_final_epoch_recheck"
    ),
    "AC-04-04": (
        "authority_fence.ac0404."
        "same_epoch_marker_cas_contention"
    ),
    "AC-04-05": (
        "authority_fence.ac0405."
        "stale_admission_marker_cas"
    ),
    "AC-04-06": (
        "authority_fence.ac0406."
        "migration_lock_loss_before_marker"
    ),
}

_DECISIONS = frozenset({"ACCEPTED", "REJECTED"})
_DISPOSITIONS = frozenset(
    {
        "WRITE_COMMITTED",
        # Internal-only: the cutover contract deliberately does not
        # ratify a public stale-legacy-write error code.
        "FENCED_STALE_EPOCH",
        "MARKER_COMMITTED",
        "CUTOVER_EPOCH_CONTENDED",
        "MIGRATION_LOCK_LOST",
    }
)
_ERROR_CODES = frozenset(
    {
        "CUTOVER_EPOCH_CONTENDED",
        "MIGRATION_LOCK_LOST",
    }
)
_CONTENDER_DISPOSITIONS = frozenset(
    {
        "MARKER_COMMITTED",
        "CUTOVER_EPOCH_CONTENDED",
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


def _require_optional_error(
    value: Any,
    path: str,
) -> str | None:
    if value is None:
        return None

    error_code = _require_enum(
        value,
        _ERROR_CODES,
        path,
        output=True,
    )
    return error_code


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


def _validate_contender(
    value: Any,
    path: str,
    *,
    output: bool,
) -> dict[str, str]:
    contender = require_mapping(value, path)
    required = (
        {"contender_id", "disposition"}
        if output
        else {"contender_id"}
    )
    require_exact_fields(
        contender,
        required,
        path=path,
    )

    result = {
        "contender_id": require_string(
            contender["contender_id"],
            f"{path}.contender_id",
        )
    }
    if output:
        result["disposition"] = _require_enum(
            contender["disposition"],
            _CONTENDER_DISPOSITIONS,
            f"{path}.disposition",
            output=True,
        )
    return result


def validate_authority_fence_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the fixture-specific, injected AC-04 inputs."""

    base = _base_fixture_id(fixture_id)
    inputs = require_mapping(raw_inputs, "inputs")

    if base in {
        "AC-04-01",
        "AC-04-02",
        "AC-04-03",
    }:
        require_exact_fields(
            inputs,
            {
                "lease_epoch",
                "committed_epoch",
                "final_epoch",
                "lease_valid",
                "write_id",
            },
            path="inputs",
        )
        result = {
            "lease_epoch": require_nonnegative_int(
                inputs["lease_epoch"],
                "inputs.lease_epoch",
            ),
            "committed_epoch": require_nonnegative_int(
                inputs["committed_epoch"],
                "inputs.committed_epoch",
            ),
            "final_epoch": require_nonnegative_int(
                inputs["final_epoch"],
                "inputs.final_epoch",
            ),
            "lease_valid": require_bool(
                inputs["lease_valid"],
                "inputs.lease_valid",
            ),
            "write_id": require_string(
                inputs["write_id"],
                "inputs.write_id",
            ),
        }

        if (
            base == "AC-04-01"
            and (
                not result["lease_valid"]
                or result["lease_epoch"]
                != result["committed_epoch"]
                or result["lease_epoch"]
                != result["final_epoch"]
            )
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "AC-04-01 requires a valid lease and "
                    "an unchanged epoch"
                ),
            )

        if (
            base == "AC-04-02"
            and (
                not result["lease_valid"]
                or result["lease_epoch"]
                >= result["committed_epoch"]
                or result["final_epoch"]
                != result["committed_epoch"]
            )
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "AC-04-02 requires a valid stale lease "
                    "against the committed epoch"
                ),
            )

        if (
            base == "AC-04-03"
            and (
                not result["lease_valid"]
                or result["lease_epoch"]
                != result["committed_epoch"]
                or result["final_epoch"]
                <= result["lease_epoch"]
            )
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "AC-04-03 requires an epoch change only "
                    "between the earlier and final checks"
                ),
            )

        return result

    if base == "AC-04-04":
        require_exact_fields(
            inputs,
            {
                "admission_epoch",
                "phase",
                "contenders",
            },
            path="inputs",
        )
        contenders = [
            _validate_contender(
                value,
                f"inputs.contenders[{index}]",
                output=False,
            )
            for index, value in enumerate(
                _require_list(
                    inputs["contenders"],
                    "inputs.contenders",
                )
            )
        ]
        if len(contenders) != 2:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                "AC-04-04 requires exactly two contenders",
            )
        if contenders[0]["contender_id"] == contenders[1]["contender_id"]:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                "AC-04-04 contender IDs must be distinct",
            )

        return {
            "admission_epoch": require_nonnegative_int(
                inputs["admission_epoch"],
                "inputs.admission_epoch",
            ),
            "phase": require_string(
                inputs["phase"],
                "inputs.phase",
            ),
            "contenders": contenders,
        }

    if base == "AC-04-05":
        require_exact_fields(
            inputs,
            {
                "admission_epoch",
                "persisted_epoch",
                "phase",
                "contender_id",
            },
            path="inputs",
        )
        admission_epoch = require_nonnegative_int(
            inputs["admission_epoch"],
            "inputs.admission_epoch",
        )
        persisted_epoch = require_nonnegative_int(
            inputs["persisted_epoch"],
            "inputs.persisted_epoch",
        )
        if persisted_epoch <= admission_epoch:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "AC-04-05 requires a persisted epoch "
                    "newer than admission"
                ),
            )

        return {
            "admission_epoch": admission_epoch,
            "persisted_epoch": persisted_epoch,
            "phase": require_string(
                inputs["phase"],
                "inputs.phase",
            ),
            "contender_id": require_string(
                inputs["contender_id"],
                "inputs.contender_id",
            ),
        }

    require_exact_fields(
        inputs,
        {
            "admission_epoch",
            "committed_epoch",
            "lock_renewed",
            "admission_record",
        },
        path="inputs",
    )
    result = {
        "admission_epoch": require_nonnegative_int(
            inputs["admission_epoch"],
            "inputs.admission_epoch",
        ),
        "committed_epoch": require_nonnegative_int(
            inputs["committed_epoch"],
            "inputs.committed_epoch",
        ),
        "lock_renewed": require_bool(
            inputs["lock_renewed"],
            "inputs.lock_renewed",
        ),
        "admission_record": require_string(
            inputs["admission_record"],
            "inputs.admission_record",
        ),
    }
    if result["lock_renewed"]:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            "AC-04-06 requires migration-lock renewal loss",
        )
    return result


def validate_authority_fence_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the common observable AC-04 outcome envelope."""

    _base_fixture_id(fixture_id)
    output = require_mapping(raw_output, "output")
    require_exact_fields(
        output,
        {
            "decision",
            "error_code",
            "disposition",
            "committed_epoch",
            "marker_count",
            "legacy_write_mutations",
            "final_recheck_performed",
            "retry_count",
            "contenders",
        },
        path="output",
    )

    contenders = [
        _validate_contender(
            value,
            f"output.contenders[{index}]",
            output=True,
        )
        for index, value in enumerate(
            _require_list(
                output["contenders"],
                "output.contenders",
                output=True,
            )
        )
    ]
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
    disposition = _require_enum(
        output["disposition"],
        _DISPOSITIONS,
        "output.disposition",
        output=True,
    )

    if (
        decision == "ACCEPTED"
        and error_code is not None
    ):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            "accepted output must not carry an error code",
        )

    if (
        decision == "REJECTED"
        and disposition == "CUTOVER_EPOCH_CONTENDED"
        and error_code != "CUTOVER_EPOCH_CONTENDED"
    ):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            "contention requires CUTOVER_EPOCH_CONTENDED",
        )

    if (
        decision == "REJECTED"
        and disposition == "MIGRATION_LOCK_LOST"
        and error_code != "MIGRATION_LOCK_LOST"
    ):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            "lock loss requires MIGRATION_LOCK_LOST",
        )

    if (
        disposition == "FENCED_STALE_EPOCH"
        and error_code is not None
    ):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "FENCED_STALE_EPOCH is an internal "
                "disposition, not a public error code"
            ),
        )

    return {
        "decision": decision,
        "error_code": error_code,
        "disposition": disposition,
        "committed_epoch": require_nonnegative_int(
            output["committed_epoch"],
            "output.committed_epoch",
        ),
        "marker_count": require_nonnegative_int(
            output["marker_count"],
            "output.marker_count",
        ),
        "legacy_write_mutations": require_nonnegative_int(
            output["legacy_write_mutations"],
            "output.legacy_write_mutations",
        ),
        "final_recheck_performed": require_bool(
            output["final_recheck_performed"],
            "output.final_recheck_performed",
        ),
        "retry_count": require_nonnegative_int(
            output["retry_count"],
            "output.retry_count",
        ),
        "contenders": contenders,
    }


def _output(
    *,
    decision: str,
    error_code: str | None,
    disposition: str,
    committed_epoch: int,
    marker_count: int = 0,
    legacy_write_mutations: int = 0,
    final_recheck_performed: bool = False,
    retry_count: int = 0,
    contenders: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "decision": decision,
        "error_code": error_code,
        "disposition": disposition,
        "committed_epoch": committed_epoch,
        "marker_count": marker_count,
        "legacy_write_mutations": legacy_write_mutations,
        "final_recheck_performed": final_recheck_performed,
        "retry_count": retry_count,
        "contenders": (
            [] if contenders is None else contenders
        ),
    }


class AuthorityFenceOracle:
    """Pure AC-04 oracle over the frozen, injected fence facts."""

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

        if self._base == "AC-04-01":
            return _output(
                decision="ACCEPTED",
                error_code=None,
                disposition="WRITE_COMMITTED",
                committed_epoch=raw_inputs["final_epoch"],
                legacy_write_mutations=1,
                final_recheck_performed=True,
            )

        if self._base == "AC-04-02":
            return _output(
                decision="REJECTED",
                error_code=None,
                disposition="FENCED_STALE_EPOCH",
                committed_epoch=raw_inputs["committed_epoch"],
                final_recheck_performed=True,
            )

        if self._base == "AC-04-03":
            return _output(
                decision="REJECTED",
                error_code=None,
                disposition="FENCED_STALE_EPOCH",
                committed_epoch=raw_inputs["final_epoch"],
                final_recheck_performed=True,
            )

        if self._base == "AC-04-04":
            first = raw_inputs["contenders"][0]["contender_id"]
            second = raw_inputs["contenders"][1]["contender_id"]
            return _output(
                decision="ACCEPTED",
                error_code=None,
                disposition="MARKER_COMMITTED",
                committed_epoch=(
                    raw_inputs["admission_epoch"] + 1
                ),
                marker_count=1,
                contenders=[
                    {
                        "contender_id": first,
                        "disposition": "MARKER_COMMITTED",
                    },
                    {
                        "contender_id": second,
                        "disposition": (
                            "CUTOVER_EPOCH_CONTENDED"
                        ),
                    },
                ],
            )

        if self._base == "AC-04-05":
            return _output(
                decision="REJECTED",
                error_code="CUTOVER_EPOCH_CONTENDED",
                disposition="CUTOVER_EPOCH_CONTENDED",
                committed_epoch=raw_inputs["persisted_epoch"],
                contenders=[
                    {
                        "contender_id": raw_inputs[
                            "contender_id"
                        ],
                        "disposition": (
                            "CUTOVER_EPOCH_CONTENDED"
                        ),
                    }
                ],
            )

        return _output(
            decision="REJECTED",
            error_code="MIGRATION_LOCK_LOST",
            disposition="MIGRATION_LOCK_LOST",
            committed_epoch=raw_inputs["committed_epoch"],
        )


class AuthorityFenceSubjectAdapter:
    """Reference AC-04 adapter; marker contention uses isolated SQLite."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = (
            f"authority_fence.{label}.reference"
        )
        self.fixture_ids = frozenset({base_fixture_id})

    def _database_path(
        self,
        context: IsolatedDomainContext,
    ) -> Any:
        path = context.root / "ac04-fence.sqlite"
        if path.exists():
            raise DomainContractError(
                "DOMAIN_ROOT_INVALID",
                "AC-04 SQLite database already exists",
            )
        return path

    def _initialize_database(
        self,
        context: IsolatedDomainContext,
        *,
        epoch: int,
        phase: str,
        unique_epoch: bool = True,
    ) -> Any:
        path = self._database_path(context)
        connection = sqlite3.connect(str(path))
        try:
            marker_epoch_column = (
                "authority_epoch INTEGER NOT NULL UNIQUE"
                if unique_epoch
                else "authority_epoch INTEGER NOT NULL"
            )
            connection.execute(
                """
                CREATE TABLE authority_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    authority_epoch INTEGER NOT NULL,
                    phase TEXT NOT NULL
                )
                """
            )
            connection.execute(
                f"""
                CREATE TABLE authority_marker (
                    phase TEXT NOT NULL,
                    contender_id TEXT NOT NULL,
                    {marker_epoch_column}
                )
                """
            )
            connection.execute(
                """
                INSERT INTO authority_state (
                    singleton,
                    authority_epoch,
                    phase
                ) VALUES (1, ?, ?)
                """,
                (epoch, phase),
            )
            connection.commit()
        finally:
            connection.close()
        return path

    def _read_database(
        self,
        path: Any,
    ) -> tuple[int, int]:
        connection = sqlite3.connect(str(path))
        try:
            epoch = connection.execute(
                """
                SELECT authority_epoch
                FROM authority_state
                WHERE singleton = 1
                """
            ).fetchone()[0]
            marker_count = connection.execute(
                "SELECT COUNT(*) FROM authority_marker"
            ).fetchone()[0]
            return int(epoch), int(marker_count)
        finally:
            connection.close()

    def _attempt_marker(
        self,
        path: Any,
        *,
        admission_epoch: int,
        phase: str,
        contender_id: str,
    ) -> bool:
        connection = sqlite3.connect(str(path))
        try:
            connection.execute("BEGIN IMMEDIATE")
            persisted_epoch, persisted_phase = (
                connection.execute(
                    """
                    SELECT authority_epoch, phase
                    FROM authority_state
                    WHERE singleton = 1
                    """
                ).fetchone()
            )
            if (
                persisted_epoch != admission_epoch
                or persisted_phase != phase
            ):
                connection.execute("ROLLBACK")
                return False

            successor = persisted_epoch + 1
            connection.execute(
                """
                INSERT INTO authority_marker (
                    authority_epoch,
                    phase,
                    contender_id
                ) VALUES (?, ?, ?)
                """,
                (successor, phase, contender_id),
            )
            connection.execute(
                """
                UPDATE authority_state
                SET authority_epoch = ?
                WHERE singleton = 1
                """,
                (successor,),
            )
            connection.execute("COMMIT")
            return True
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _execute_marker_contention(
        self,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> dict[str, Any]:
        admission_epoch = raw_inputs["admission_epoch"]
        phase = raw_inputs["phase"]
        path = self._initialize_database(
            context,
            epoch=admission_epoch,
            phase=phase,
        )
        contenders: list[dict[str, str]] = []

        for contender in raw_inputs["contenders"]:
            committed = self._attempt_marker(
                path,
                admission_epoch=admission_epoch,
                phase=phase,
                contender_id=contender["contender_id"],
            )
            contenders.append(
                {
                    "contender_id": contender["contender_id"],
                    "disposition": (
                        "MARKER_COMMITTED"
                        if committed
                        else "CUTOVER_EPOCH_CONTENDED"
                    ),
                }
            )

        epoch, marker_count = self._read_database(path)
        return _output(
            decision="ACCEPTED",
            error_code=None,
            disposition="MARKER_COMMITTED",
            committed_epoch=epoch,
            marker_count=marker_count,
            contenders=contenders,
        )

    def _execute_stale_admission(
        self,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> dict[str, Any]:
        path = self._initialize_database(
            context,
            epoch=raw_inputs["persisted_epoch"],
            phase=raw_inputs["phase"],
        )
        committed = self._attempt_marker(
            path,
            admission_epoch=raw_inputs["admission_epoch"],
            phase=raw_inputs["phase"],
            contender_id=raw_inputs["contender_id"],
        )
        epoch, marker_count = self._read_database(path)
        if committed:
            raise DomainContractError(
                "DOMAIN_ADAPTER_INVALID",
                "reference stale-admission CAS unexpectedly committed",
            )

        return _output(
            decision="REJECTED",
            error_code="CUTOVER_EPOCH_CONTENDED",
            disposition="CUTOVER_EPOCH_CONTENDED",
            committed_epoch=epoch,
            marker_count=marker_count,
            contenders=[
                {
                    "contender_id": raw_inputs["contender_id"],
                    "disposition": (
                        "CUTOVER_EPOCH_CONTENDED"
                    ),
                }
            ],
        )

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

        if self._base == "AC-04-01":
            return _output(
                decision="ACCEPTED",
                error_code=None,
                disposition="WRITE_COMMITTED",
                committed_epoch=raw_inputs["final_epoch"],
                legacy_write_mutations=1,
                final_recheck_performed=True,
            )

        if self._base == "AC-04-02":
            return _output(
                decision="REJECTED",
                error_code=None,
                disposition="FENCED_STALE_EPOCH",
                committed_epoch=raw_inputs["committed_epoch"],
                final_recheck_performed=True,
            )

        if self._base == "AC-04-03":
            return _output(
                decision="REJECTED",
                error_code=None,
                disposition="FENCED_STALE_EPOCH",
                committed_epoch=raw_inputs["final_epoch"],
                final_recheck_performed=True,
            )

        if self._base == "AC-04-04":
            return self._execute_marker_contention(
                raw_inputs,
                context,
            )

        if self._base == "AC-04-05":
            return self._execute_stale_admission(
                raw_inputs,
                context,
            )

        return _output(
            decision="REJECTED",
            error_code="MIGRATION_LOCK_LOST",
            disposition="MIGRATION_LOCK_LOST",
            committed_epoch=raw_inputs["committed_epoch"],
        )


class FaultInjectedAuthorityFenceAdapter(
    AuthorityFenceSubjectAdapter
):
    """Six fixture-specific AC-04 defects."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        super().__init__(base_fixture_id)
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = (
            f"authority_fence.{label}.fault_injected"
        )
        self.fixture_ids = frozenset(
            {f"{base_fixture_id}{_NEGATIVE_SUFFIX}"}
        )

    def _faulty_two_marker_commits(
        self,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> dict[str, Any]:
        path = self._initialize_database(
            context,
            epoch=raw_inputs["admission_epoch"],
            phase=raw_inputs["phase"],
            unique_epoch=False,
        )
        connection = sqlite3.connect(str(path))
        try:
            for contender in raw_inputs["contenders"]:
                connection.execute("BEGIN IMMEDIATE")
                persisted_epoch = connection.execute(
                    """
                    SELECT authority_epoch
                    FROM authority_state
                    WHERE singleton = 1
                    """
                ).fetchone()[0]
                successor = persisted_epoch + 1
                connection.execute(
                    """
                    INSERT INTO authority_marker (
                        authority_epoch,
                        phase,
                        contender_id
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        successor,
                        raw_inputs["phase"],
                        contender["contender_id"],
                    ),
                )
                connection.execute(
                    """
                    UPDATE authority_state
                    SET authority_epoch = ?
                    WHERE singleton = 1
                    """,
                    (successor,),
                )
                connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

        epoch, marker_count = self._read_database(path)
        return _output(
            decision="ACCEPTED",
            error_code=None,
            disposition="MARKER_COMMITTED",
            committed_epoch=epoch,
            marker_count=marker_count,
            contenders=[
                {
                    "contender_id": contender["contender_id"],
                    "disposition": "MARKER_COMMITTED",
                }
                for contender in raw_inputs["contenders"]
            ],
        )

    def _faulty_stale_admission_commits(
        self,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> dict[str, Any]:
        path = self._initialize_database(
            context,
            epoch=raw_inputs["persisted_epoch"],
            phase=raw_inputs["phase"],
        )
        connection = sqlite3.connect(str(path))
        try:
            connection.execute("BEGIN IMMEDIATE")
            persisted_epoch = connection.execute(
                """
                SELECT authority_epoch
                FROM authority_state
                WHERE singleton = 1
                """
            ).fetchone()[0]
            successor = persisted_epoch + 1
            connection.execute(
                """
                INSERT INTO authority_marker (
                    authority_epoch,
                    phase,
                    contender_id
                ) VALUES (?, ?, ?)
                """,
                (
                    successor,
                    raw_inputs["phase"],
                    raw_inputs["contender_id"],
                ),
            )
            connection.execute(
                """
                UPDATE authority_state
                SET authority_epoch = ?
                WHERE singleton = 1
                """,
                (successor,),
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

        epoch, marker_count = self._read_database(path)
        return _output(
            decision="ACCEPTED",
            error_code=None,
            disposition="MARKER_COMMITTED",
            committed_epoch=epoch,
            marker_count=marker_count,
            contenders=[
                {
                    "contender_id": raw_inputs["contender_id"],
                    "disposition": "MARKER_COMMITTED",
                }
            ],
        )

    def execute(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]:
        expected = f"{self._base}{_NEGATIVE_SUFFIX}"
        if fixture_id != expected:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                f"adapter={self.adapter_id};fixture_id={fixture_id}",
            )

        if self._base == "AC-04-01":
            return _output(
                decision="REJECTED",
                error_code=None,
                disposition="FENCED_STALE_EPOCH",
                committed_epoch=raw_inputs["final_epoch"],
                final_recheck_performed=True,
            )

        if self._base == "AC-04-02":
            return _output(
                decision="ACCEPTED",
                error_code=None,
                disposition="WRITE_COMMITTED",
                committed_epoch=raw_inputs["final_epoch"],
                legacy_write_mutations=1,
                final_recheck_performed=True,
            )

        if self._base == "AC-04-03":
            return _output(
                decision="ACCEPTED",
                error_code=None,
                disposition="WRITE_COMMITTED",
                committed_epoch=raw_inputs["final_epoch"],
                legacy_write_mutations=1,
                final_recheck_performed=False,
            )

        if self._base == "AC-04-04":
            return self._faulty_two_marker_commits(
                raw_inputs,
                context,
            )

        if self._base == "AC-04-05":
            return self._faulty_stale_admission_commits(
                raw_inputs,
                context,
            )

        return _output(
            decision="ACCEPTED",
            error_code=None,
            disposition="MARKER_COMMITTED",
            committed_epoch=(
                raw_inputs["committed_epoch"] + 1
            ),
            marker_count=1,
            retry_count=1,
        )


def authority_fence_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return immutable built-in AC-04 registration rows."""

    registrations: list[DomainRegistration] = []

    for base_fixture_id in _BASE_FIXTURES:
        oracle = AuthorityFenceOracle(base_fixture_id)
        positive_adapter = AuthorityFenceSubjectAdapter(
            base_fixture_id
        )
        negative_adapter = FaultInjectedAuthorityFenceAdapter(
            base_fixture_id
        )

        registrations.append(
            DomainRegistration(
                fixture_id=base_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=positive_adapter,
                input_validator=validate_authority_fence_inputs,
                output_validator=validate_authority_fence_output,
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
                adapter=negative_adapter,
                input_validator=validate_authority_fence_inputs,
                output_validator=validate_authority_fence_output,
            )
        )

    return tuple(registrations)