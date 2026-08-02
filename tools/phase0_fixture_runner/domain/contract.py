from __future__ import annotations

import copy
import hashlib
import json
import os
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

DOMAIN_CONTRACT_VERSION = 1
DOMAIN_ARTIFACT_SCHEMA_VERSION = 1

_FORBIDDEN_OUTCOME_KEYS = frozenset(
    {
        "actual",
        "actual_output",
        "claimed_outcome",
        "claimed_result",
        "dispatch",
        "domain_actual",
        "domain_expected",
        "domain_verification",
        "expected",
        "expected_output",
        "outcome",
        "required_result",
        "result",
        "selected_candidate",
        "terminal_classification",
        "unauthorized",
        "verification_result",
    }
)


class DomainContractError(Exception):
    """A deterministic, contained domain-contract failure."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class IsolatedDomainContext:
    """Runner-owned deterministic context supplied to adapters."""

    root: Path
    clock: tuple[int, ...]
    ids: tuple[str, ...]


class DomainOracle(Protocol):
    oracle_id: str
    oracle_version: int
    fixture_ids: frozenset[str]

    def compute_expected(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class DomainSubjectAdapter(Protocol):
    adapter_id: str
    adapter_version: int
    fixture_ids: frozenset[str]

    def execute(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]: ...


InputValidator = Callable[
    [str, Mapping[str, Any]],
    dict[str, Any],
]
OutputValidator = Callable[
    [str, Mapping[str, Any]],
    dict[str, Any],
]


@dataclass(frozen=True)
class DomainRegistration:
    fixture_id: str
    oracle_id: str
    oracle_version: int
    oracle: DomainOracle
    adapter: DomainSubjectAdapter
    input_validator: InputValidator
    output_validator: OutputValidator


@dataclass(frozen=True)
class DomainRunResult:
    fixture_id: str
    passed: bool
    domain_input: dict[str, Any]
    domain_actual: dict[str, Any]
    domain_expected: dict[str, Any]
    domain_verification: dict[str, Any]


def normalize_json_value(value: Any) -> Any:
    """Return an NFC-normalized canonical-JSON-compatible value."""

    if value is None or isinstance(value, bool) or type(value) is int:
        return value

    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)

    if isinstance(value, (list, tuple)):
        return [normalize_json_value(item) for item in value]

    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise DomainContractError(
                    "DOMAIN_JSON_INVALID",
                    "canonical JSON object keys must be strings",
                )

            key = unicodedata.normalize("NFC", raw_key)
            if key in normalized:
                raise DomainContractError(
                    "DOMAIN_JSON_INVALID",
                    f"normalized_duplicate_key={key}",
                )
            normalized[key] = normalize_json_value(raw_value)
        return normalized

    raise DomainContractError(
        "DOMAIN_JSON_INVALID",
        f"unsupported_type={type(value).__name__}",
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Encode canonical JSON exactly as the fixture runner does."""

    normalized = normalize_json_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_json_file_bytes(value: Any) -> bytes:
    """Encode one canonical JSON artifact with its trailing LF."""

    return canonical_json_bytes(value) + b"\n"


def sha256_bytes(raw: bytes) -> str:
    """Return a lowercase raw-byte SHA-256 digest."""

    return hashlib.sha256(raw).hexdigest()


def require_mapping(
    value: Any,
    path: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} must be an object",
        )
    return value


def require_exact_fields(
    value: Mapping[str, Any],
    required: set[str] | frozenset[str],
    optional: set[str] | frozenset[str] = frozenset(),
    *,
    path: str,
) -> None:
    missing = sorted(set(required) - set(value))
    unknown = sorted(
        set(value) - set(required) - set(optional)
    )

    if missing:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path}.missing={','.join(missing)}",
        )

    if unknown:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path}.unknown={','.join(unknown)}",
        )


def require_string(value: Any, path: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in value
        )
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} must be a non-empty printable string",
        )

    return value


def require_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} must be boolean",
        )

    return value


def require_nonnegative_int(value: Any, path: str) -> int:
    if type(value) is not int or value < 0:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} must be a non-negative integer",
        )

    return value


def reject_outcome_claims(
    value: Any,
    path: str = "inputs",
) -> None:
    """Reject outcome-shaped script inputs before either role runs."""

    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            if not isinstance(raw_key, str):
                raise DomainContractError(
                    "DOMAIN_INPUT_INVALID",
                    f"{path} contains a non-string key",
                )

            key = (
                unicodedata.normalize("NFC", raw_key)
                .lower()
                .replace("-", "_")
            )
            if key in _FORBIDDEN_OUTCOME_KEYS:
                raise DomainContractError(
                    "ORACLE_INPUT_TAINTED",
                    f"{path}.{raw_key}",
                )

            reject_outcome_claims(
                child,
                f"{path}.{raw_key}",
            )

    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            reject_outcome_claims(
                child,
                f"{path}[{index}]",
            )


class StaticDomainRegistry:
    """Immutable checked-in fixture-to-role dispatch table."""

    def __init__(
        self,
        registrations: Iterable[DomainRegistration],
    ) -> None:
        table: dict[str, DomainRegistration] = {}

        for registration in registrations:
            if registration.fixture_id in table:
                raise ValueError(
                    "duplicate domain fixture registration: "
                    f"{registration.fixture_id}"
                )

            if (
                registration.oracle_id
                != registration.oracle.oracle_id
            ):
                raise ValueError(
                    "registration/oracle ID mismatch"
                )

            if (
                registration.oracle_version
                != registration.oracle.oracle_version
            ):
                raise ValueError(
                    "registration/oracle version mismatch"
                )

            if (
                registration.fixture_id
                not in registration.oracle.fixture_ids
            ):
                raise ValueError(
                    "oracle does not declare registered fixture"
                )

            if (
                registration.fixture_id
                not in registration.adapter.fixture_ids
            ):
                raise ValueError(
                    "adapter does not declare registered fixture"
                )

            table[registration.fixture_id] = registration

        self._table = MappingProxyType(table)

    def requires_verification(
        self,
        fixture_id: str,
    ) -> bool:
        """Return whether a fixture is statically domain-verified."""

        return fixture_id in self._table

    def verify(
        self,
        domain_case: Mapping[str, Any],
        fixture_id: str,
        context: IsolatedDomainContext,
    ) -> DomainRunResult:
        """Validate, execute, compute, and compare a domain case."""

        case = require_mapping(
            domain_case,
            "domain_case",
        )
        require_exact_fields(
            case,
            {
                "contract_version",
                "fixture_id",
                "oracle_id",
                "oracle_version",
                "inputs",
            },
            path="domain_case",
        )

        contract_version = case["contract_version"]
        if (
            type(contract_version) is not int
            or contract_version != DOMAIN_CONTRACT_VERSION
        ):
            raise DomainContractError(
                "DOMAIN_CONTRACT_VERSION_UNSUPPORTED",
                (
                    f"supported={DOMAIN_CONTRACT_VERSION};"
                    f"received={contract_version}"
                ),
            )

        declared_fixture_id = require_string(
            case["fixture_id"],
            "domain_case.fixture_id",
        )
        if declared_fixture_id != fixture_id:
            raise DomainContractError(
                "DOMAIN_FIXTURE_ID_MISMATCH",
                (
                    f"cli={fixture_id};"
                    f"domain_case={declared_fixture_id}"
                ),
            )

        registration = self._table.get(fixture_id)
        if registration is None:
            raise DomainContractError(
                "DOMAIN_ORACLE_UNKNOWN",
                f"fixture_id={fixture_id}",
            )

        declared_oracle_id = require_string(
            case["oracle_id"],
            "domain_case.oracle_id",
        )
        if declared_oracle_id != registration.oracle_id:
            raise DomainContractError(
                "DOMAIN_ORACLE_UNKNOWN",
                (
                    f"fixture_id={fixture_id};"
                    f"received={declared_oracle_id}"
                ),
            )

        declared_oracle_version = case["oracle_version"]
        if (
            type(declared_oracle_version) is not int
            or declared_oracle_version
            != registration.oracle_version
        ):
            raise DomainContractError(
                "DOMAIN_ORACLE_VERSION_UNSUPPORTED",
                (
                    f"oracle_id={declared_oracle_id};"
                    f"supported={registration.oracle_version};"
                    f"received={declared_oracle_version}"
                ),
            )

        raw_inputs = require_mapping(
            case["inputs"],
            "domain_case.inputs",
        )
        reject_outcome_claims(raw_inputs)

        validated_inputs = registration.input_validator(
            fixture_id,
            copy.deepcopy(raw_inputs),
        )
        validated_inputs = normalize_json_value(
            validated_inputs
        )

        actual_raw = registration.adapter.execute(
            fixture_id,
            copy.deepcopy(validated_inputs),
            context,
        )
        expected_raw = registration.oracle.compute_expected(
            fixture_id,
            copy.deepcopy(validated_inputs),
        )

        actual = registration.output_validator(
            fixture_id,
            require_mapping(
                actual_raw,
                "domain_actual.output",
            ),
        )
        expected = registration.output_validator(
            fixture_id,
            require_mapping(
                expected_raw,
                "domain_expected.output",
            ),
        )

        actual = normalize_json_value(actual)
        expected = normalize_json_value(expected)

        passed = (
            canonical_json_bytes(actual)
            == canonical_json_bytes(expected)
        )

        domain_input = {
            "schema_version": DOMAIN_ARTIFACT_SCHEMA_VERSION,
            "contract_version": DOMAIN_CONTRACT_VERSION,
            "fixture_id": fixture_id,
            "oracle_id": registration.oracle_id,
            "oracle_version": registration.oracle_version,
            "inputs": validated_inputs,
        }
        domain_actual = {
            "schema_version": DOMAIN_ARTIFACT_SCHEMA_VERSION,
            "fixture_id": fixture_id,
            "adapter_id": registration.adapter.adapter_id,
            "adapter_version": (
                registration.adapter.adapter_version
            ),
            "output": actual,
        }
        domain_expected = {
            "schema_version": DOMAIN_ARTIFACT_SCHEMA_VERSION,
            "fixture_id": fixture_id,
            "oracle_id": registration.oracle_id,
            "oracle_version": registration.oracle_version,
            "output": expected,
        }

        input_digest = sha256_bytes(
            canonical_json_file_bytes(domain_input)
        )
        actual_digest = sha256_bytes(
            canonical_json_file_bytes(domain_actual)
        )
        expected_digest = sha256_bytes(
            canonical_json_file_bytes(domain_expected)
        )

        verification = {
            "schema_version": DOMAIN_ARTIFACT_SCHEMA_VERSION,
            "contract_version": DOMAIN_CONTRACT_VERSION,
            "fixture_id": fixture_id,
            "status": "PASS" if passed else "FAIL",
            "oracle": {
                "id": registration.oracle_id,
                "version": registration.oracle_version,
            },
            "adapter": {
                "id": registration.adapter.adapter_id,
                "version": (
                    registration.adapter.adapter_version
                ),
            },
            "comparison": {
                "canonical_outputs_equal": passed,
                "actual_output_sha256": sha256_bytes(
                    canonical_json_bytes(actual)
                ),
                "expected_output_sha256": sha256_bytes(
                    canonical_json_bytes(expected)
                ),
            },
            "artifact_digests": {
                "domain_input_raw_sha256": input_digest,
                "domain_actual_raw_sha256": actual_digest,
                "domain_expected_raw_sha256": expected_digest,
            },
        }

        return DomainRunResult(
            fixture_id=fixture_id,
            passed=passed,
            domain_input=domain_input,
            domain_actual=domain_actual,
            domain_expected=domain_expected,
            domain_verification=verification,
        )


def write_domain_artifacts(
    root: Path,
    result: DomainRunResult,
) -> dict[str, Any]:
    """Write domain evidence inside the runner-owned fresh root."""

    if not root.is_dir():
        raise DomainContractError(
            "DOMAIN_ROOT_INVALID",
            "runner-owned domain root does not exist",
        )

    documents = {
        "domain_input": (
            "domain-input.json",
            result.domain_input,
        ),
        "domain_actual": (
            "domain-actual.json",
            result.domain_actual,
        ),
        "domain_expected": (
            "domain-expected.json",
            result.domain_expected,
        ),
        "domain_verification": (
            "domain-verification.json",
            result.domain_verification,
        ),
    }

    artifact_paths: dict[str, str] = {}
    digests: dict[str, str] = {}

    for key, (relative_path, document) in documents.items():
        path = root / relative_path
        raw = canonical_json_file_bytes(document)

        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

        written = path.read_bytes()
        if written != raw:
            raise DomainContractError(
                "DOMAIN_ARTIFACT_DIGEST_MISMATCH",
                f"artifact={relative_path}",
            )

        artifact_paths[key] = relative_path
        digests[f"{key}_raw_sha256"] = sha256_bytes(
            written
        )

    return {
        "declared": True,
        "required": True,
        "status": "PASS" if result.passed else "FAIL",
        "fixture_id": result.fixture_id,
        "oracle": copy.deepcopy(
            result.domain_verification["oracle"]
        ),
        "adapter": copy.deepcopy(
            result.domain_verification["adapter"]
        ),
        "artifact_paths": artifact_paths,
        "digests": digests,
    }