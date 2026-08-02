from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from .contract import (
    DomainContractError,
    DomainRegistration,
    IsolatedDomainContext,
    canonical_json_bytes,
    require_exact_fields,
    require_mapping,
    require_nonnegative_int,
    require_string,
)

_BASE_FIXTURES = (
    "RT-04",
    "RT-05",
    "RT-06",
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "RT-04": "routing.rt04.exclusion",
    "RT-05": (
        "routing.rt05.deterministic_tie_selection"
    ),
    "RT-06": (
        "routing.rt06.pre_dispatch_drift"
    ),
}

_CANDIDATE_ROLES = frozenset(
    {
        "ELIGIBLE",
        "EXCLUDED",
        "TERMINAL",
    }
)
_EXCLUSION_REASONS = frozenset(
    {
        "EXCLUDED",
        "TERMINAL_TIER",
    }
)

# RT-04 deliberately accepts role tags only.
# An eligible candidate has fixed unit weight 1;
# this fixture does not define a general weighting policy.


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


def _require_sha256_hex(
    value: Any,
    path: str,
) -> str:
    digest = require_string(
        value,
        path,
    )
    if (
        len(digest) != 64
        or any(
            character
            not in "0123456789abcdef"
            for character in digest
        )
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                f"{path} must be 64 lowercase "
                "hexadecimal characters"
            ),
        )

    return digest


def _validate_candidate_ids(
    value: Any,
    path: str,
    *,
    minimum: int,
) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) < minimum
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                f"{path} must contain at least "
                f"{minimum} candidates"
            ),
        )

    candidates = [
        require_string(
            item,
            f"{path}[{index}]",
        )
        for index, item in enumerate(value)
    ]
    if len(set(candidates)) != len(candidates):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                f"{path} contains duplicate "
                "candidate IDs"
            ),
        )

    return candidates


def validate_routing_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one closed routing input schema."""

    base = _base_fixture_id(fixture_id)
    inputs = require_mapping(
        raw_inputs,
        "inputs",
    )

    if base == "RT-04":
        require_exact_fields(
            inputs,
            {
                "candidates",
            },
            path="inputs",
        )

        raw_candidates = inputs["candidates"]
        if (
            not isinstance(
                raw_candidates,
                list,
            )
            or len(raw_candidates) < 3
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "inputs.candidates must "
                    "contain at least three rows"
                ),
            )

        candidates: list[
            dict[str, str]
        ] = []
        seen: set[str] = set()
        observed_roles: set[str] = set()

        for index, raw_candidate in enumerate(
            raw_candidates
        ):
            path = (
                f"inputs.candidates[{index}]"
            )
            candidate = require_mapping(
                raw_candidate,
                path,
            )
            require_exact_fields(
                candidate,
                {
                    "candidate_id",
                    "role",
                },
                path=path,
            )

            candidate_id = require_string(
                candidate["candidate_id"],
                f"{path}.candidate_id",
            )
            if candidate_id in seen:
                raise DomainContractError(
                    "DOMAIN_INPUT_INVALID",
                    (
                        "duplicate candidate_id="
                        f"{candidate_id}"
                    ),
                )

            role = require_string(
                candidate["role"],
                f"{path}.role",
            )
            if role not in _CANDIDATE_ROLES:
                raise DomainContractError(
                    "DOMAIN_INPUT_INVALID",
                    (
                        f"{path}.role "
                        f"unsupported={role}"
                    ),
                )

            seen.add(candidate_id)
            observed_roles.add(role)
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "role": role,
                }
            )

        if (
            observed_roles
            != set(_CANDIDATE_ROLES)
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "inputs.candidates must cover "
                    "ELIGIBLE, EXCLUDED, and "
                    "TERMINAL"
                ),
            )

        return {
            "candidates": candidates,
        }

    if base == "RT-05":
        require_exact_fields(
            inputs,
            {
                "request_id",
                "snapshot_digest",
                "candidate_set",
            },
            path="inputs",
        )

        return {
            "request_id": require_string(
                inputs["request_id"],
                "inputs.request_id",
            ),
            "snapshot_digest": (
                _require_sha256_hex(
                    inputs["snapshot_digest"],
                    "inputs.snapshot_digest",
                )
            ),
            "candidate_set": (
                _validate_candidate_ids(
                    inputs["candidate_set"],
                    "inputs.candidate_set",
                    minimum=2,
                )
            ),
        }

    require_exact_fields(
        inputs,
        {
            "frozen_configuration_revision",
            "current_configuration_revision",
        },
        path="inputs",
    )

    return {
        "frozen_configuration_revision": (
            require_nonnegative_int(
                inputs[
                    "frozen_configuration_revision"
                ],
                (
                    "inputs."
                    "frozen_configuration_revision"
                ),
            )
        ),
        "current_configuration_revision": (
            require_nonnegative_int(
                inputs[
                    "current_configuration_revision"
                ],
                (
                    "inputs."
                    "current_configuration_revision"
                ),
            )
        ),
    }


def _validate_optional_exclusion_reason(
    value: Any,
    path: str,
) -> str | None:
    if value is None:
        return None

    reason = require_string(
        value,
        path,
    )
    if reason not in _EXCLUSION_REASONS:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            f"{path} unsupported={reason}",
        )

    return reason


def validate_routing_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate oracle and subject routing output."""

    base = _base_fixture_id(fixture_id)
    output = require_mapping(
        raw_output,
        "output",
    )

    if base == "RT-04":
        require_exact_fields(
            output,
            {
                "candidates",
                "selectable_candidates",
            },
            path="output",
        )

        raw_candidates = output["candidates"]
        if (
            not isinstance(
                raw_candidates,
                list,
            )
            or not raw_candidates
        ):
            raise DomainContractError(
                "DOMAIN_OUTPUT_INVALID",
                (
                    "output.candidates must be "
                    "a non-empty array"
                ),
            )

        candidates: list[
            dict[str, Any]
        ] = []
        seen: set[str] = set()

        for index, raw_candidate in enumerate(
            raw_candidates
        ):
            path = (
                f"output.candidates[{index}]"
            )
            candidate = require_mapping(
                raw_candidate,
                path,
            )
            require_exact_fields(
                candidate,
                {
                    "candidate_id",
                    "weight",
                    "exclusion_reason",
                },
                path=path,
            )

            candidate_id = require_string(
                candidate["candidate_id"],
                f"{path}.candidate_id",
            )
            if candidate_id in seen:
                raise DomainContractError(
                    "DOMAIN_OUTPUT_INVALID",
                    (
                        "duplicate output "
                        "candidate_id="
                        f"{candidate_id}"
                    ),
                )

            seen.add(candidate_id)
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "weight": (
                        require_nonnegative_int(
                            candidate["weight"],
                            f"{path}.weight",
                        )
                    ),
                    "exclusion_reason": (
                        _validate_optional_exclusion_reason(
                            candidate[
                                "exclusion_reason"
                            ],
                            (
                                f"{path}."
                                "exclusion_reason"
                            ),
                        )
                    ),
                }
            )

        selectable = _validate_candidate_ids(
            output["selectable_candidates"],
            "output.selectable_candidates",
            minimum=1,
        )

        return {
            "candidates": candidates,
            "selectable_candidates": selectable,
        }

    if base == "RT-05":
        require_exact_fields(
            output,
            {
                "audit_seed",
                "selection_index",
                "ordered_candidates",
                "selected_candidate",
            },
            path="output",
        )

        ordered = _validate_candidate_ids(
            output["ordered_candidates"],
            "output.ordered_candidates",
            minimum=2,
        )

        return {
            "audit_seed": (
                _require_sha256_hex(
                    output["audit_seed"],
                    "output.audit_seed",
                )
            ),
            "selection_index": (
                require_nonnegative_int(
                    output["selection_index"],
                    "output.selection_index",
                )
            ),
            "ordered_candidates": ordered,
            "selected_candidate": (
                require_string(
                    output["selected_candidate"],
                    "output.selected_candidate",
                )
            ),
        }

    require_exact_fields(
        output,
        {
            "result",
            "dispatch_count",
            "replanning_input_revision",
        },
        path="output",
    )

    result = require_string(
        output["result"],
        "output.result",
    )
    if result not in {
        "CONFIGURATION_STALE",
        "DISPATCH_ADMITTED",
    }:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "output.result "
                f"unsupported={result}"
            ),
        )

    replanning = output[
        "replanning_input_revision"
    ]
    if replanning is not None:
        replanning = require_nonnegative_int(
            replanning,
            (
                "output."
                "replanning_input_revision"
            ),
        )

    return {
        "result": result,
        "dispatch_count": (
            require_nonnegative_int(
                output["dispatch_count"],
                "output.dispatch_count",
            )
        ),
        "replanning_input_revision": (
            replanning
        ),
    }


class RoutingOracle:
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

        if self._base == "RT-04":
            return self._rt04(raw_inputs)
        if self._base == "RT-05":
            return self._rt05(raw_inputs)

        return self._rt06(raw_inputs)

    def _rt04(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        rows: list[
            dict[str, Any]
        ] = []
        selectable: list[str] = []

        for candidate in inputs["candidates"]:
            candidate_id = candidate[
                "candidate_id"
            ]
            role = candidate["role"]

            if role == "ELIGIBLE":
                weight = 1
                reason = None
                selectable.append(candidate_id)
            elif role == "EXCLUDED":
                weight = 0
                reason = "EXCLUDED"
            else:
                weight = 0
                reason = "TERMINAL_TIER"

            rows.append(
                {
                    "candidate_id": candidate_id,
                    "weight": weight,
                    "exclusion_reason": reason,
                }
            )

        return {
            "candidates": sorted(
                rows,
                key=lambda row: (
                    row["candidate_id"]
                ),
            ),
            "selectable_candidates": sorted(
                selectable
            ),
        }

    def _rt05(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        seed_input = {
            "request_id": inputs["request_id"],
            "snapshot_digest": (
                inputs["snapshot_digest"]
            ),
        }
        seed = hashlib.sha256(
            canonical_json_bytes(seed_input)
        ).digest()

        ordered = sorted(
            inputs["candidate_set"]
        )
        index = (
            int.from_bytes(
                seed[:8],
                byteorder="big",
                signed=False,
            )
            % len(ordered)
        )

        return {
            "audit_seed": seed.hex(),
            "selection_index": index,
            "ordered_candidates": ordered,
            "selected_candidate": (
                ordered[index]
            ),
        }

    def _rt06(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        frozen = inputs[
            "frozen_configuration_revision"
        ]
        current = inputs[
            "current_configuration_revision"
        ]

        if frozen != current:
            return {
                "result": (
                    "CONFIGURATION_STALE"
                ),
                "dispatch_count": 0,
                "replanning_input_revision": (
                    current
                ),
            }

        return {
            "result": "DISPATCH_ADMITTED",
            "dispatch_count": 1,
            "replanning_input_revision": None,
        }


class RoutingSubjectAdapter:
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
            f"routing.{label}.reference"
        )
        self.fixture_ids = frozenset(
            {
                base_fixture_id,
            }
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

        if self._base == "RT-04":
            return self._rt04(raw_inputs)
        if self._base == "RT-05":
            return self._rt05(raw_inputs)

        return self._rt06(raw_inputs)

    def _rt04(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        weighted: dict[
            str,
            dict[str, Any],
        ] = {}
        selectable: list[str] = []

        for candidate in inputs["candidates"]:
            candidate_id = candidate[
                "candidate_id"
            ]
            role = candidate["role"]

            if role == "ELIGIBLE":
                weighted[candidate_id] = {
                    "candidate_id": candidate_id,
                    "weight": 1,
                    "exclusion_reason": None,
                }
                selectable.append(candidate_id)
            elif role == "TERMINAL":
                weighted[candidate_id] = {
                    "candidate_id": candidate_id,
                    "weight": 0,
                    "exclusion_reason": (
                        "TERMINAL_TIER"
                    ),
                }
            else:
                weighted[candidate_id] = {
                    "candidate_id": candidate_id,
                    "weight": 0,
                    "exclusion_reason": (
                        "EXCLUDED"
                    ),
                }

        return {
            "candidates": [
                weighted[key]
                for key in sorted(weighted)
            ],
            "selectable_candidates": sorted(
                selectable
            ),
        }

    def _rt05(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        audit_document = {
            "snapshot_digest": (
                inputs["snapshot_digest"]
            ),
            "request_id": inputs["request_id"],
        }
        digest = hashlib.sha256(
            canonical_json_bytes(
                audit_document
            )
        ).digest()

        candidates = list(
            inputs["candidate_set"]
        )
        candidates.sort()

        position = (
            int.from_bytes(
                digest[0:8],
                "big",
            )
            % len(candidates)
        )

        return {
            "audit_seed": digest.hex(),
            "selection_index": position,
            "ordered_candidates": candidates,
            "selected_candidate": (
                candidates[position]
            ),
        }

    def _rt06(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        current = inputs[
            "current_configuration_revision"
        ]

        if (
            inputs[
                "frozen_configuration_revision"
            ]
            == current
        ):
            result = "DISPATCH_ADMITTED"
            count = 1
            replan = None
        else:
            result = "CONFIGURATION_STALE"
            count = 0
            replan = current

        return {
            "result": result,
            "dispatch_count": count,
            "replanning_input_revision": replan,
        }


class FaultInjectedRoutingAdapter:
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
            f"routing.{label}.fault_injected"
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

        if self._base == "RT-04":
            return self._rt04(raw_inputs)
        if self._base == "RT-05":
            return self._rt05(raw_inputs)

        return self._rt06(raw_inputs)

    def _rt04(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        rows: list[
            dict[str, Any]
        ] = []
        selectable: list[str] = []

        for candidate in inputs["candidates"]:
            candidate_id = candidate[
                "candidate_id"
            ]
            role = candidate["role"]

            if role == "ELIGIBLE":
                weight = 1
                reason = None
                selectable.append(candidate_id)
            elif role == "EXCLUDED":
                weight = 0
                reason = "EXCLUDED"
            else:
                # Fault: terminal exclusion is
                # recorded, but its weight remains
                # nonzero.
                weight = 1
                reason = "TERMINAL_TIER"

            rows.append(
                {
                    "candidate_id": candidate_id,
                    "weight": weight,
                    "exclusion_reason": reason,
                }
            )

        return {
            "candidates": sorted(
                rows,
                key=lambda row: (
                    row["candidate_id"]
                ),
            ),
            "selectable_candidates": sorted(
                selectable
            ),
        }

    def _rt05(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        seed_input = {
            "request_id": inputs["request_id"],
            "snapshot_digest": (
                inputs["snapshot_digest"]
            ),
        }
        seed = hashlib.sha256(
            canonical_json_bytes(seed_input)
        ).digest()

        # Fault: selection indexes the raw input
        # order instead of the sorted candidate set.
        candidates = list(
            inputs["candidate_set"]
        )
        index = (
            int.from_bytes(
                seed[:8],
                "big",
            )
            % len(candidates)
        )

        return {
            "audit_seed": seed.hex(),
            "selection_index": index,
            "ordered_candidates": candidates,
            "selected_candidate": (
                candidates[index]
            ),
        }

    def _rt06(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        del inputs

        # Fault: configuration drift is ignored
        # and dispatch is admitted.
        return {
            "result": "DISPATCH_ADMITTED",
            "dispatch_count": 1,
            "replanning_input_revision": None,
        }


def routing_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return immutable built-in RT registry rows."""

    registrations: list[
        DomainRegistration
    ] = []

    for base_fixture_id in _BASE_FIXTURES:
        oracle = RoutingOracle(
            base_fixture_id
        )
        positive_adapter = (
            RoutingSubjectAdapter(
                base_fixture_id
            )
        )
        negative_adapter = (
            FaultInjectedRoutingAdapter(
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
                    validate_routing_inputs
                ),
                output_validator=(
                    validate_routing_output
                ),
            )
        )

        negative_fixture_id = (
            f"{base_fixture_id}"
            f"{_NEGATIVE_SUFFIX}"
        )
        registrations.append(
            DomainRegistration(
                fixture_id=negative_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=(
                    oracle.oracle_version
                ),
                oracle=oracle,
                adapter=negative_adapter,
                input_validator=(
                    validate_routing_inputs
                ),
                output_validator=(
                    validate_routing_output
                ),
            )
        )

    return tuple(registrations)