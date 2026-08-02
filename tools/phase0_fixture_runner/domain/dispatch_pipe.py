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
    "DP-01",
    "DP-02",
    "DP-03",
    "DP-04",
    "DP-05",
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "DP-01": (
        "dispatch_pipe.dp01.separate_layer_receipts"
    ),
    "DP-02": (
        "dispatch_pipe.dp02.pre_spawn_rejection"
    ),
    "DP-03": (
        "dispatch_pipe.dp03.nonzero_exit_uncertain"
    ),
    "DP-04": (
        "dispatch_pipe.dp04.output_cap_exceeded"
    ),
    "DP-05": (
        "dispatch_pipe.dp05.deadline_tree_kill"
    ),
}

_CASE_KINDS = frozenset(
    {
        "LAYERED_SUCCESS",
        "PRE_SPAWN_REJECTION",
        "NONZERO_EXIT",
        "OUTPUT_CAP",
        "HARD_DEADLINE",
    }
)
_RULE_TIERS = frozenset(
    {
        "OBS",
        "CANDIDATE",
    }
)
_TERMINAL_CATEGORIES = frozenset(
    {
        "EXITED",
        "PRE_SPAWN_REJECTED",
        "OUTPUT_CAP_EXCEEDED",
        "PROCESS_DEADLINE",
    }
)
_EFFECT_CERTAINTIES = frozenset(
    {
        "NOT_STARTED",
        "MAY_HAVE_STARTED",
        "STARTED",
    }
)
_EXECUTION_DISPOSITIONS = frozenset(
    {
        "TERMINAL_RESULT_DELIVERED",
        "NOT_STARTED",
        "EXECUTION_UNCERTAIN",
        "OUTPUT_BOUNDED_STOP",
        "PROCESS_TIMEOUT",
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


def _validate_chunk_record(
    value: Any,
    path: str,
) -> dict[str, Any]:
    chunk = require_mapping(value, path)
    require_exact_fields(
        chunk,
        {
            "chunk_id",
            "byte_count",
            "frame_complete",
        },
        path=path,
    )

    byte_count = require_nonnegative_int(
        chunk["byte_count"],
        f"{path}.byte_count",
    )
    if byte_count == 0:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path}.byte_count must be positive",
        )

    return {
        "chunk_id": require_string(
            chunk["chunk_id"],
            f"{path}.chunk_id",
        ),
        "byte_count": byte_count,
        "frame_complete": require_bool(
            chunk["frame_complete"],
            f"{path}.frame_complete",
        ),
    }


def _total_output_bytes(
    facts: Mapping[str, Any],
) -> int:
    return sum(
        chunk["byte_count"]
        for chunk in facts["chunk_records"]
    )


def _validate_fixture_vector(
    fixture_id: str,
    facts: Mapping[str, Any],
) -> None:
    base_fixture_id = _base_fixture_id(fixture_id)
    chunks = facts["chunk_records"]
    total_bytes = _total_output_bytes(facts)

    def invalid(detail: str) -> None:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{base_fixture_id}.{detail}",
        )

    if base_fixture_id == "DP-01":
        if facts["case_kind"] != "LAYERED_SUCCESS":
            invalid("requires LAYERED_SUCCESS")
        if not facts["validation_accepted"]:
            invalid("requires accepted validation")
        if not facts["spawn_observed"]:
            invalid("requires observed process spawn")
        if len(chunks) != 8:
            invalid("requires eight flushed chunks")
        if not all(
            chunk["byte_count"] == 1
            and chunk["frame_complete"]
            for chunk in chunks
        ):
            invalid(
                "requires eight complete one-byte frames"
            )
        if (
            not facts["exit_observed"]
            or facts["exit_code"] != 0
        ):
            invalid("requires observed exit code 0")
        if facts["output_limit_bytes"] is not None:
            invalid("layered-success vector has no output cap")
        if (
            facts["deadline_reached"]
            or facts["tree_kill_observed"]
        ):
            invalid("normal completion cannot carry stop evidence")
        return

    if base_fixture_id == "DP-02":
        if facts["case_kind"] != "PRE_SPAWN_REJECTION":
            invalid("requires PRE_SPAWN_REJECTION")
        if facts["validation_accepted"]:
            invalid("requires rejected validation")
        if (
            facts["spawn_observed"]
            or chunks
            or facts["exit_observed"]
            or facts["exit_code"] is not None
            or facts["output_limit_bytes"] is not None
            or facts["deadline_reached"]
            or facts["tree_kill_observed"]
        ):
            invalid(
                "pre-spawn rejection cannot carry "
                "process-execution evidence"
            )
        return

    if base_fixture_id == "DP-03":
        if facts["case_kind"] != "NONZERO_EXIT":
            invalid("requires NONZERO_EXIT")
        if not facts["validation_accepted"]:
            invalid("requires accepted validation")
        if not facts["spawn_observed"]:
            invalid("requires observed process creation")
        if chunks:
            invalid("nonzero-exit vector isolates exit evidence")
        if (
            not facts["exit_observed"]
            or facts["exit_code"] in {None, 0}
        ):
            invalid("requires an observed nonzero exit")
        if facts["output_limit_bytes"] is not None:
            invalid("nonzero-exit vector has no output cap")
        if (
            facts["deadline_reached"]
            or facts["tree_kill_observed"]
        ):
            invalid("nonzero-exit vector cannot be a timeout")
        return

    if base_fixture_id == "DP-04":
        if facts["case_kind"] != "OUTPUT_CAP":
            invalid("requires OUTPUT_CAP")
        if not facts["validation_accepted"]:
            invalid("requires accepted validation")
        if not facts["spawn_observed"]:
            invalid("requires observed process creation")
        if len(chunks) < 2:
            invalid("requires multiple output chunks")
        limit = facts["output_limit_bytes"]
        if limit is None or limit == 0:
            invalid("requires a positive output limit")
        if total_bytes <= limit:
            invalid("accumulated output must exceed the limit")
        if facts["exit_observed"]:
            invalid(
                "output-cap terminal category precedes exit receipt"
            )
        if facts["deadline_reached"]:
            invalid("output cap must be distinct from deadline")
        if not facts["tree_kill_observed"]:
            invalid("bounded process stop must be observed")
        return

    if facts["case_kind"] != "HARD_DEADLINE":
        invalid("requires HARD_DEADLINE")
    if not facts["validation_accepted"]:
        invalid("requires accepted validation")
    if not facts["spawn_observed"]:
        invalid("requires observed process creation")
    if chunks:
        invalid("deadline vector isolates timeout evidence")
    if facts["output_limit_bytes"] is not None:
        invalid("deadline vector has no output cap")
    if not facts["deadline_reached"]:
        invalid("requires observed hard deadline")
    if not facts["tree_kill_observed"]:
        invalid("requires observed process-tree kill")
    if (
        not facts["exit_observed"]
        or facts["exit_code"] != 15
    ):
        invalid("requires observed cleanup exit code 15")


def validate_dispatch_pipe_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one closed DP-01..05 dispatch-pipe vector.

    DP-01 and DP-04 use explicitly proposed CANDIDATE shapes.
    DP-02, DP-03, and DP-05 are closed over supplied OBS facts.
    """

    _base_fixture_id(fixture_id)
    inputs = require_mapping(raw_inputs, "inputs")
    require_exact_fields(
        inputs,
        {
            "case_kind",
            "validation_accepted",
            "spawn_observed",
            "process_identity_token",
            "chunk_records",
            "exit_observed",
            "exit_code",
            "output_limit_bytes",
            "deadline_reached",
            "tree_kill_observed",
        },
        path="inputs",
    )

    spawn_observed = require_bool(
        inputs["spawn_observed"],
        "inputs.spawn_observed",
    )
    identity_token = _require_nullable_string(
        inputs["process_identity_token"],
        "inputs.process_identity_token",
    )
    if spawn_observed != (identity_token is not None):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "inputs.process_identity_token must be present "
                "exactly when spawn is observed"
            ),
        )

    raw_chunks = inputs["chunk_records"]
    if not isinstance(raw_chunks, list):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            "inputs.chunk_records must be an array",
        )
    chunks = [
        _validate_chunk_record(
            chunk,
            f"inputs.chunk_records[{index}]",
        )
        for index, chunk in enumerate(raw_chunks)
    ]
    chunk_ids = [
        chunk["chunk_id"]
        for chunk in chunks
    ]
    if len(chunk_ids) != len(set(chunk_ids)):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            "inputs.chunk_records contains duplicate chunk IDs",
        )
    if chunks and not spawn_observed:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            "output chunks require an observed spawn",
        )

    exit_observed = require_bool(
        inputs["exit_observed"],
        "inputs.exit_observed",
    )
    exit_code = _require_nullable_int(
        inputs["exit_code"],
        "inputs.exit_code",
    )
    if exit_observed != (exit_code is not None):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "inputs.exit_code must be present exactly "
                "when exit is observed"
            ),
        )

    deadline_reached = require_bool(
        inputs["deadline_reached"],
        "inputs.deadline_reached",
    )
    tree_kill_observed = require_bool(
        inputs["tree_kill_observed"],
        "inputs.tree_kill_observed",
    )
    if (
        deadline_reached or tree_kill_observed
    ) and not spawn_observed:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            "deadline/kill evidence requires an observed spawn",
        )

    normalized = {
        "case_kind": _require_enum(
            inputs["case_kind"],
            _CASE_KINDS,
            "inputs.case_kind",
        ),
        "validation_accepted": require_bool(
            inputs["validation_accepted"],
            "inputs.validation_accepted",
        ),
        "spawn_observed": spawn_observed,
        "process_identity_token": identity_token,
        "chunk_records": chunks,
        "exit_observed": exit_observed,
        "exit_code": exit_code,
        "output_limit_bytes": _require_nullable_int(
            inputs["output_limit_bytes"],
            "inputs.output_limit_bytes",
        ),
        "deadline_reached": deadline_reached,
        "tree_kill_observed": tree_kill_observed,
    }

    _validate_fixture_vector(fixture_id, normalized)
    return normalized


def _validate_process_evidence(
    value: Any,
    path: str,
) -> dict[str, Any]:
    evidence = require_mapping(value, path)
    require_exact_fields(
        evidence,
        {
            "recorded",
            "spawn_observed",
            "process_creation_count",
            "identity_token",
            "stop_requested",
            "tree_kill_observed",
        },
        path=path,
    )

    return {
        "recorded": require_bool(
            evidence["recorded"],
            f"{path}.recorded",
        ),
        "spawn_observed": require_bool(
            evidence["spawn_observed"],
            f"{path}.spawn_observed",
        ),
        "process_creation_count": require_nonnegative_int(
            evidence["process_creation_count"],
            f"{path}.process_creation_count",
        ),
        "identity_token": _require_nullable_string(
            evidence["identity_token"],
            f"{path}.identity_token",
        ),
        "stop_requested": require_bool(
            evidence["stop_requested"],
            f"{path}.stop_requested",
        ),
        "tree_kill_observed": require_bool(
            evidence["tree_kill_observed"],
            f"{path}.tree_kill_observed",
        ),
    }


def _validate_protocol_evidence(
    value: Any,
    path: str,
) -> dict[str, Any]:
    evidence = require_mapping(value, path)
    require_exact_fields(
        evidence,
        {
            "recorded",
            "chunk_count",
            "byte_count",
            "all_frames_complete",
            "output_limit_bytes",
            "output_cap_exceeded",
        },
        path=path,
    )

    return {
        "recorded": require_bool(
            evidence["recorded"],
            f"{path}.recorded",
        ),
        "chunk_count": require_nonnegative_int(
            evidence["chunk_count"],
            f"{path}.chunk_count",
        ),
        "byte_count": require_nonnegative_int(
            evidence["byte_count"],
            f"{path}.byte_count",
        ),
        "all_frames_complete": require_bool(
            evidence["all_frames_complete"],
            f"{path}.all_frames_complete",
        ),
        "output_limit_bytes": _require_nullable_int(
            evidence["output_limit_bytes"],
            f"{path}.output_limit_bytes",
        ),
        "output_cap_exceeded": require_bool(
            evidence["output_cap_exceeded"],
            f"{path}.output_cap_exceeded",
        ),
    }


def _validate_completion_evidence(
    value: Any,
    path: str,
) -> dict[str, Any]:
    evidence = require_mapping(value, path)
    require_exact_fields(
        evidence,
        {
            "recorded",
            "exit_observed",
            "exit_code",
            "deadline_reached",
        },
        path=path,
    )

    return {
        "recorded": require_bool(
            evidence["recorded"],
            f"{path}.recorded",
        ),
        "exit_observed": require_bool(
            evidence["exit_observed"],
            f"{path}.exit_observed",
        ),
        "exit_code": _require_nullable_int(
            evidence["exit_code"],
            f"{path}.exit_code",
        ),
        "deadline_reached": require_bool(
            evidence["deadline_reached"],
            f"{path}.deadline_reached",
        ),
    }


def validate_dispatch_pipe_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate separate dispatch-pipe evidence-layer output."""

    _base_fixture_id(fixture_id)
    output = require_mapping(raw_output, "output")
    require_exact_fields(
        output,
        {
            "rule_tier",
            "terminal_category",
            "effect_certainty",
            "execution_disposition",
            "verified_task_success",
            "layers_separately_recorded",
            "process_evidence",
            "protocol_evidence",
            "completion_evidence",
        },
        path="output",
    )

    return {
        "rule_tier": _require_enum(
            output["rule_tier"],
            _RULE_TIERS,
            "output.rule_tier",
            output=True,
        ),
        "terminal_category": _require_enum(
            output["terminal_category"],
            _TERMINAL_CATEGORIES,
            "output.terminal_category",
            output=True,
        ),
        "effect_certainty": _require_enum(
            output["effect_certainty"],
            _EFFECT_CERTAINTIES,
            "output.effect_certainty",
            output=True,
        ),
        "execution_disposition": _require_enum(
            output["execution_disposition"],
            _EXECUTION_DISPOSITIONS,
            "output.execution_disposition",
            output=True,
        ),
        "verified_task_success": require_bool(
            output["verified_task_success"],
            "output.verified_task_success",
        ),
        "layers_separately_recorded": require_bool(
            output["layers_separately_recorded"],
            "output.layers_separately_recorded",
        ),
        "process_evidence": _validate_process_evidence(
            output["process_evidence"],
            "output.process_evidence",
        ),
        "protocol_evidence": _validate_protocol_evidence(
            output["protocol_evidence"],
            "output.protocol_evidence",
        ),
        "completion_evidence": (
            _validate_completion_evidence(
                output["completion_evidence"],
                "output.completion_evidence",
            )
        ),
    }


def _build_output(
    facts: Mapping[str, Any],
    *,
    rule_tier: str,
    terminal_category: str,
    effect_certainty: str,
    execution_disposition: str,
    verified_task_success: bool,
    layers_separately_recorded: bool,
    stop_requested: bool,
) -> dict[str, Any]:
    chunks = facts["chunk_records"]
    limit = facts["output_limit_bytes"]
    total_bytes = _total_output_bytes(facts)

    return {
        "rule_tier": rule_tier,
        "terminal_category": terminal_category,
        "effect_certainty": effect_certainty,
        "execution_disposition": execution_disposition,
        "verified_task_success": verified_task_success,
        "layers_separately_recorded": (
            layers_separately_recorded
        ),
        "process_evidence": {
            "recorded": True,
            "spawn_observed": facts["spawn_observed"],
            "process_creation_count": (
                1 if facts["spawn_observed"] else 0
            ),
            "identity_token": facts[
                "process_identity_token"
            ],
            "stop_requested": stop_requested,
            "tree_kill_observed": facts[
                "tree_kill_observed"
            ],
        },
        "protocol_evidence": {
            "recorded": bool(chunks),
            "chunk_count": len(chunks),
            "byte_count": total_bytes,
            "all_frames_complete": bool(
                chunks
                and all(
                    chunk["frame_complete"]
                    for chunk in chunks
                )
            ),
            "output_limit_bytes": limit,
            "output_cap_exceeded": bool(
                limit is not None
                and total_bytes > limit
            ),
        },
        "completion_evidence": {
            "recorded": True,
            "exit_observed": facts["exit_observed"],
            "exit_code": facts["exit_code"],
            "deadline_reached": facts[
                "deadline_reached"
            ],
        },
    }


def _oracle_evaluate(
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    case_kind = facts["case_kind"]

    if case_kind == "LAYERED_SUCCESS":
        return _build_output(
            facts,
            rule_tier="CANDIDATE",
            terminal_category="EXITED",
            effect_certainty="STARTED",
            execution_disposition=(
                "TERMINAL_RESULT_DELIVERED"
            ),
            verified_task_success=True,
            layers_separately_recorded=True,
            stop_requested=False,
        )

    if case_kind == "PRE_SPAWN_REJECTION":
        return _build_output(
            facts,
            rule_tier="OBS",
            terminal_category="PRE_SPAWN_REJECTED",
            effect_certainty="NOT_STARTED",
            execution_disposition="NOT_STARTED",
            verified_task_success=False,
            layers_separately_recorded=False,
            stop_requested=False,
        )

    if case_kind == "NONZERO_EXIT":
        return _build_output(
            facts,
            rule_tier="OBS",
            terminal_category="EXITED",
            effect_certainty="STARTED",
            execution_disposition="EXECUTION_UNCERTAIN",
            verified_task_success=False,
            layers_separately_recorded=False,
            stop_requested=False,
        )

    if case_kind == "OUTPUT_CAP":
        return _build_output(
            facts,
            rule_tier="CANDIDATE",
            terminal_category="OUTPUT_CAP_EXCEEDED",
            effect_certainty="STARTED",
            execution_disposition="OUTPUT_BOUNDED_STOP",
            verified_task_success=False,
            layers_separately_recorded=False,
            stop_requested=True,
        )

    return _build_output(
        facts,
        rule_tier="OBS",
        terminal_category="PROCESS_DEADLINE",
        effect_certainty="STARTED",
        execution_disposition="PROCESS_TIMEOUT",
        verified_task_success=False,
        layers_separately_recorded=False,
        stop_requested=True,
    )


def _subject_evaluate(
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    kind = facts["case_kind"]

    if kind == "LAYERED_SUCCESS":
        tier = "CANDIDATE"
        category = "EXITED"
        certainty = "STARTED"
        disposition = "TERMINAL_RESULT_DELIVERED"
        success = True
        separate = True
        stop = False
    elif kind == "PRE_SPAWN_REJECTION":
        tier = "OBS"
        category = "PRE_SPAWN_REJECTED"
        certainty = "NOT_STARTED"
        disposition = "NOT_STARTED"
        success = False
        separate = False
        stop = False
    elif kind == "NONZERO_EXIT":
        tier = "OBS"
        category = "EXITED"
        certainty = "STARTED"
        disposition = "EXECUTION_UNCERTAIN"
        success = False
        separate = False
        stop = False
    elif kind == "OUTPUT_CAP":
        tier = "CANDIDATE"
        category = "OUTPUT_CAP_EXCEEDED"
        certainty = "STARTED"
        disposition = "OUTPUT_BOUNDED_STOP"
        success = False
        separate = False
        stop = True
    else:
        tier = "OBS"
        category = "PROCESS_DEADLINE"
        certainty = "STARTED"
        disposition = "PROCESS_TIMEOUT"
        success = False
        separate = False
        stop = True

    return _build_output(
        facts,
        rule_tier=tier,
        terminal_category=category,
        effect_certainty=certainty,
        execution_disposition=disposition,
        verified_task_success=success,
        layers_separately_recorded=separate,
        stop_requested=stop,
    )


class DispatchPipeOracle:
    """Pure DP-01..05 oracle over injected process facts."""

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


class DispatchPipeSubjectAdapter:
    """Pure structurally independent dispatch-pipe adapter."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = (
            f"dispatch_pipe.{label}.reference"
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


class FaultInjectedDispatchPipeAdapter(
    DispatchPipeSubjectAdapter
):
    """One genuine dispatch-pipe defect per negative."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        super().__init__(base_fixture_id)
        faults = {
            "DP-01": "omit_protocol_layer_receipt",
            "DP-02": "make_pre_spawn_rejection_ambiguous",
            "DP-03": "claim_nonzero_exit_verified_success",
            "DP-04": "misclassify_output_cap_as_deadline",
            "DP-05": "discard_cleanup_exit_evidence",
        }
        self._fault = faults[base_fixture_id]
        self._fixture_id = (
            f"{base_fixture_id}{_NEGATIVE_SUFFIX}"
        )
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = (
            f"dispatch_pipe.{label}.{self._fault}"
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

        output = _subject_evaluate(raw_inputs)

        if self._base == "DP-01":
            output["protocol_evidence"]["recorded"] = False
            output["layers_separately_recorded"] = False
            return output

        if self._base == "DP-02":
            output["effect_certainty"] = "MAY_HAVE_STARTED"
            return output

        if self._base == "DP-03":
            output["verified_task_success"] = True
            return output

        if self._base == "DP-04":
            output["terminal_category"] = "PROCESS_DEADLINE"
            output["execution_disposition"] = "PROCESS_TIMEOUT"
            return output

        output["completion_evidence"]["exit_observed"] = False
        output["completion_evidence"]["exit_code"] = None
        return output


def dispatch_pipe_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return all DP-01..05 dispatch-pipe registrations."""

    registrations: list[DomainRegistration] = []

    for base_fixture_id in _BASE_FIXTURES:
        oracle = DispatchPipeOracle(base_fixture_id)
        registrations.append(
            DomainRegistration(
                fixture_id=base_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=DispatchPipeSubjectAdapter(
                    base_fixture_id
                ),
                input_validator=validate_dispatch_pipe_inputs,
                output_validator=validate_dispatch_pipe_output,
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
                adapter=FaultInjectedDispatchPipeAdapter(
                    base_fixture_id
                ),
                input_validator=validate_dispatch_pipe_inputs,
                output_validator=validate_dispatch_pipe_output,
            )
        )

    return tuple(registrations)
