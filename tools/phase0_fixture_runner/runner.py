from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import os
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from domain import (
    DOMAIN_REGISTRY,
    DomainContractError,
    IsolatedDomainContext,
    write_domain_artifacts,
)

CONTRACT_NAME = "CONTROLLED-FAKE-RUNNER-CONTRACT-R2"
SCRIPT_SCHEMA_VERSION = 1
SUPPORTED_SCRIPT_SCHEMA_VERSIONS = frozenset({1, 2})
TRANSCRIPT_SCHEMA_VERSION = 1
FIXTURE_RECORD_SCHEMA_VERSION = 1
DOMAIN_FIXTURE_RECORD_SCHEMA_VERSION = 2

SUPPORTED_EVENTS = frozenset(
    {
        "INTENT_PERSISTED",
        "SPAWNED",
        "CHUNK",
        "EXIT",
        "SILENCE",
        "PROCESS_DEADLINE",
        "CANCEL_ACK",
        "TREE_STATE",
        "CLEANUP_ERROR",
    }
)

_FIXTURE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class InvalidInvocationError(Exception):
    """Raised when CLI arguments cannot describe a fresh fixture run."""


class _ScriptParseError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class _ContractViolation(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class _UnsupportedEvent(Exception):
    def __init__(self, event_type: str) -> None:
        super().__init__(event_type)
        self.event_type = event_type


@dataclass(frozen=True)
class _PreparedScript:
    schema_version: int
    clock: tuple[int, ...]
    ids: tuple[str, ...]
    events: tuple[Any, ...]
    interrupt_after_append: int | None
    expect: dict[str, Any] | None
    domain_case: dict[str, Any] | None

    @property
    def run_id(self) -> str:
        return self.ids[0]


def _reject_float(raw: str) -> Any:
    raise _ScriptParseError("JSON_PARSE_ERROR", f"non_integer_number={raw}")


def _reject_constant(raw: str) -> Any:
    raise _ScriptParseError("JSON_PARSE_ERROR", f"non_json_number={raw}")


def _object_without_duplicates(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _ScriptParseError(
                "JSON_PARSE_ERROR",
                f"duplicate_key={key}",
            )
        result[key] = value
    return result


def _normalize_value(value: Any) -> Any:
    if value is None or isinstance(value, bool) or type(value) is int:
        return value

    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)

    if isinstance(value, list):
        return [_normalize_value(item) for item in value]

    if isinstance(value, tuple):
        return [_normalize_value(item) for item in value]

    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise TypeError("canonical JSON object keys must be strings")

            key = unicodedata.normalize("NFC", raw_key)
            if key in normalized:
                raise _ScriptParseError(
                    "JSON_PARSE_ERROR",
                    f"normalized_duplicate_key={key}",
                )
            normalized[key] = _normalize_value(raw_value)
        return normalized

    raise TypeError(
        f"unsupported canonical JSON type: {type(value).__name__}"
    )


def _parse_json_bytes(raw: bytes) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _ScriptParseError(
            "JSON_PARSE_ERROR",
            f"invalid_utf8_at_byte={exc.start}",
        ) from exc

    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        raise _ScriptParseError(
            "JSON_PARSE_ERROR",
            (
                f"line={exc.lineno};column={exc.colno};"
                f"position={exc.pos}"
            ),
        ) from exc

    return _normalize_value(parsed)


def _canonical_json_bytes(value: Any) -> bytes:
    normalized = _normalize_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _write_new_bytes(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _write_new_json(path: Path, value: Any) -> None:
    _write_new_bytes(path, _canonical_json_bytes(value) + b"\n")


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _valid_token(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 256
        and all(
            ord(character) >= 32 and ord(character) != 127
            for character in value
        )
    )


def _prepare_script(parsed: Any) -> _PreparedScript:
    if not isinstance(parsed, dict):
        raise _ContractViolation(
            "SCRIPT_NOT_OBJECT",
            "top-level JSON must be an object",
        )

    # JSON framing and parsing have completed before this first
    # version/schema decision.
    schema_version = parsed.get("schema_version")
    if (
        type(schema_version) is not int
        or schema_version not in SUPPORTED_SCRIPT_SCHEMA_VERSIONS
    ):
        raise _ContractViolation(
            "SCRIPT_VERSION_UNSUPPORTED",
            (
                "supported=1,2;"
                f"received={schema_version}"
            ),
        )

    allowed = {
        "schema_version",
        "clock",
        "ids",
        "events",
        "interrupt_after_append",
        "expect",
    }
    if schema_version == 2:
        allowed.add("domain_case")
    unknown = sorted(set(parsed) - allowed)
    if unknown:
        raise _ContractViolation(
            "UNKNOWN_SCRIPT_FIELD",
            ",".join(unknown),
        )

    events = parsed.get("events")
    clock = parsed.get("clock")
    ids = parsed.get("ids")

    if not isinstance(events, list):
        raise _ContractViolation(
            "INVALID_EVENTS",
            "events must be an array",
        )
    if not isinstance(clock, list):
        raise _ContractViolation(
            "INVALID_CLOCK",
            "clock must be an array",
        )
    if not isinstance(ids, list):
        raise _ContractViolation(
            "INVALID_ID_SOURCE",
            "ids must be an array",
        )

    if len(clock) != len(events):
        raise _ContractViolation(
            "CLOCK_CARDINALITY_MISMATCH",
            f"clock={len(clock)};events={len(events)}",
        )
    if len(ids) != len(events) + 1:
        raise _ContractViolation(
            "ID_CARDINALITY_MISMATCH",
            f"ids={len(ids)};required={len(events) + 1}",
        )

    previous: int | None = None
    for index, tick in enumerate(clock):
        if type(tick) is not int or tick < 0:
            raise _ContractViolation(
                "INVALID_CLOCK_VALUE",
                f"index={index};value={tick}",
            )
        if previous is not None and tick < previous:
            raise _ContractViolation(
                "NON_MONOTONIC_CLOCK",
                (
                    f"index={index};previous={previous};"
                    f"value={tick}"
                ),
            )
        previous = tick

    for index, token in enumerate(ids):
        if not _valid_token(token):
            raise _ContractViolation(
                "INVALID_DETERMINISTIC_ID",
                f"index={index}",
            )
    if len(set(ids)) != len(ids):
        raise _ContractViolation(
            "DUPLICATE_DETERMINISTIC_ID",
            "all injected IDs must be unique",
        )

    interrupt = parsed.get("interrupt_after_append")
    if interrupt is not None:
        if (
            type(interrupt) is not int
            or not 0 <= interrupt < len(events)
        ):
            raise _ContractViolation(
                "INVALID_INTERRUPT_INDEX",
                f"value={interrupt};events={len(events)}",
            )

        # A simulated crash stops the event producer. Later observations
        # require a separate, explicit reconciliation fixture rather than
        # being silently processed as if the producer had survived.
        if interrupt != len(events) - 1:
            raise _ContractViolation(
                "EVENTS_AFTER_SIMULATED_INTERRUPTION",
                "the interruption must be the final scripted input event",
            )

    expect = parsed.get("expect")
    if expect is not None and not isinstance(expect, dict):
        raise _ContractViolation(
            "INVALID_EXPECTATIONS",
            "expect must be an object",
        )

    domain_case = parsed.get("domain_case")
    if domain_case is not None and not isinstance(domain_case, dict):
        raise _ContractViolation(
            "INVALID_DOMAIN_CASE",
            "domain_case must be an object",
        )

    return _PreparedScript(
        schema_version=schema_version,
        clock=tuple(clock),
        ids=tuple(ids),
        events=tuple(events),
        interrupt_after_append=interrupt,
        expect=copy.deepcopy(expect),
        domain_case=copy.deepcopy(domain_case),
    )


def _run_id_hint(parsed: Any) -> str | None:
    if not isinstance(parsed, dict):
        return None

    ids = parsed.get("ids")
    if isinstance(ids, list) and ids and _valid_token(ids[0]):
        return ids[0]
    return None


def _input_event_count_hint(parsed: Any) -> int:
    if (
        isinstance(parsed, dict)
        and isinstance(parsed.get("events"), list)
    ):
        return len(parsed["events"])
    return 0


def _initial_state(
    fixture_id: str,
    run_id: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "fixture_id": fixture_id,
        "run_id": run_id,
        "last_sequence": -1,
        "applied_event_ids": [],
        "intent_persisted": False,
        "idempotency_bindings": [],
        "idempotency_result": None,
        "spawned": False,
        "processes": {},
        "chunks": [],
        "stream_raw_sha256": _sha256_bytes(b""),
        "exit_code": None,
        "terminal_classification": None,
        "terminal_events": [],
        "terminal_receipt": False,
        "effect_certainty": "NOT_STARTED",
        "execution_outcome": "PENDING",
        "cancel_ack": False,
        "tree_states": [],
        "cleanup_errors": [],
        "recovery": {
            "simulated_interruption": False,
            "interrupted_after_sequence": None,
            "reducer_replay_idempotent": None,
            "external_dispatch_replayed": False,
            "confirmation_required": False,
        },
    }


def _require_exact_fields(
    event: dict[str, Any],
    required: set[str],
    allowed: set[str],
) -> None:
    missing = sorted(required - set(event))
    if missing:
        raise _ContractViolation(
            "MISSING_EVENT_FIELD",
            ",".join(missing),
        )

    unknown = sorted(set(event) - allowed)
    if unknown:
        raise _ContractViolation(
            "UNKNOWN_EVENT_FIELD",
            ",".join(unknown),
        )


def _validate_identity(identity: Any) -> dict[str, Any]:
    if not isinstance(identity, dict):
        raise _ContractViolation(
            "INVALID_PROCESS_IDENTITY",
            "identity must be an object",
        )

    token = identity.get("token")
    if not _valid_token(token):
        raise _ContractViolation(
            "INVALID_PROCESS_IDENTITY",
            "identity.token must be a stable token",
        )

    if "pid" in identity and type(identity["pid"]) is not int:
        raise _ContractViolation(
            "INVALID_PROCESS_IDENTITY",
            "identity.pid must be an integer",
        )

    return copy.deepcopy(identity)


def _event_time(
    event: dict[str, Any],
    record: dict[str, Any],
) -> int:
    tick = event.get("t")
    if type(tick) is not int or tick < 0:
        raise _ContractViolation(
            "INVALID_EVENT_TIME",
            f"value={tick}",
        )

    observed = record["observed_monotonic"]
    if tick != observed:
        raise _ContractViolation(
            "TIME_SOURCE_MISMATCH",
            f"event_t={tick};injected_clock={observed}",
        )

    return tick


def _record_terminal(
    state: dict[str, Any],
    classification: str,
    sequence: int,
    source: str,
    outcome: str,
    certainty: str,
) -> None:
    state["terminal_events"].append(
        {
            "classification": classification,
            "sequence": sequence,
            "source": source,
        }
    )

    # The first primary terminal result is immutable. Later process or
    # cleanup observations remain evidence and cannot replace it.
    if state["terminal_classification"] is None:
        state["terminal_classification"] = classification
        state["terminal_receipt"] = True
        state["execution_outcome"] = outcome
        state["effect_certainty"] = certainty


def _reduce_intent(
    state: dict[str, Any],
    event: dict[str, Any],
    sequence: int,
) -> None:
    allowed = {
        "type",
        "client_id",
        "command_type",
        "idempotency_key",
        "payload",
    }
    _require_exact_fields(event, {"type"}, allowed)
    state["intent_persisted"] = True

    binding_fields = (
        "client_id",
        "command_type",
        "idempotency_key",
        "payload",
    )
    present = [field in event for field in binding_fields]
    if any(present) and not all(present):
        raise _ContractViolation(
            "INCOMPLETE_IDEMPOTENCY_BINDING",
            (
                "client_id, command_type, idempotency_key, and "
                "payload are all required"
            ),
        )

    if not all(present):
        return

    for field in binding_fields[:3]:
        if not _valid_token(event[field]):
            raise _ContractViolation(
                "INVALID_IDEMPOTENCY_BINDING",
                f"{field} must be a stable token",
            )

    payload_sha256 = _sha256_bytes(
        _canonical_json_bytes(event["payload"])
    )
    identity = {
        "client_id": event["client_id"],
        "command_type": event["command_type"],
        "idempotency_key": event["idempotency_key"],
    }

    existing = next(
        (
            item
            for item in state["idempotency_bindings"]
            if all(
                item[field] == identity[field]
                for field in identity
            )
        ),
        None,
    )

    if existing is None:
        state["idempotency_bindings"].append(
            {
                **identity,
                "payload_sha256": payload_sha256,
            }
        )
        return

    if existing["payload_sha256"] == payload_sha256:
        state["idempotency_result"] = "IDEMPOTENCY_HIT"
        _record_terminal(
            state,
            "IDEMPOTENCY_HIT",
            sequence,
            "INTENT_PERSISTED",
            "SUCCEEDED",
            "NOT_STARTED",
        )
    else:
        state["idempotency_result"] = (
            "IDEMPOTENCY_PAYLOAD_MISMATCH"
        )
        _record_terminal(
            state,
            "IDEMPOTENCY_PAYLOAD_MISMATCH",
            sequence,
            "INTENT_PERSISTED",
            "REJECTED",
            "NOT_STARTED",
        )


def _reduce_record(
    state: dict[str, Any],
    record: dict[str, Any],
) -> dict[str, Any]:
    event_id = record["event_id"]
    if event_id in state["applied_event_ids"]:
        return copy.deepcopy(state)

    sequence = record["sequence"]
    if sequence != state["last_sequence"] + 1:
        raise _ContractViolation(
            "JOURNAL_SEQUENCE_GAP",
            (
                f"expected={state['last_sequence'] + 1};"
                f"received={sequence}"
            ),
        )

    event = record["event"]
    if not isinstance(event, dict):
        raise _ContractViolation(
            "EVENT_NOT_OBJECT",
            f"sequence={sequence}",
        )

    event_type = event.get("type")
    if not isinstance(event_type, str):
        raise _ContractViolation(
            "MISSING_EVENT_TYPE",
            f"sequence={sequence}",
        )
    if event_type not in SUPPORTED_EVENTS:
        raise _UnsupportedEvent(event_type)

    next_state = copy.deepcopy(state)

    if event_type == "INTENT_PERSISTED":
        _reduce_intent(next_state, event, sequence)

    elif event_type == "SPAWNED":
        _require_exact_fields(
            event,
            {"type", "identity"},
            {"type", "identity"},
        )
        identity = _validate_identity(event["identity"])
        token = identity["token"]
        prior = next_state["processes"].get(token)
        if prior is not None and prior != identity:
            raise _ContractViolation(
                "PROCESS_IDENTITY_TOKEN_REUSED",
                f"token={token}",
            )

        next_state["processes"][token] = identity
        next_state["spawned"] = True
        next_state["effect_certainty"] = "STARTED"
        if next_state["terminal_classification"] is None:
            next_state["execution_outcome"] = "RUNNING"

    elif event_type == "CHUNK":
        _require_exact_fields(
            event,
            {"type", "bytes", "t"},
            {"type", "bytes", "t"},
        )
        encoded = event["bytes"]
        if not isinstance(encoded, str):
            raise _ContractViolation(
                "INVALID_CHUNK_BYTES",
                "bytes must be base64 text",
            )

        try:
            raw = base64.b64decode(
                encoded.encode("ascii"),
                validate=True,
            )
        except (UnicodeEncodeError, binascii.Error) as exc:
            raise _ContractViolation(
                "INVALID_CHUNK_BYTES",
                "bytes must be canonical base64 text",
            ) from exc

        if base64.b64encode(raw).decode("ascii") != encoded:
            raise _ContractViolation(
                "INVALID_CHUNK_BYTES",
                "bytes must use canonical base64 padding",
            )

        tick = _event_time(event, record)
        next_state["chunks"].append(
            {
                "bytes": encoded,
                "byte_count": len(raw),
                "raw_sha256": _sha256_bytes(raw),
                "t": tick,
            }
        )

        stream_raw = b"".join(
            base64.b64decode(
                item["bytes"].encode("ascii"),
                validate=True,
            )
            for item in next_state["chunks"]
        )
        next_state["stream_raw_sha256"] = _sha256_bytes(
            stream_raw
        )
        next_state["effect_certainty"] = "STARTED"
        if next_state["terminal_classification"] is None:
            next_state["execution_outcome"] = "RUNNING"

    elif event_type == "EXIT":
        _require_exact_fields(
            event,
            {"type", "code"},
            {"type", "code"},
        )
        code = event["code"]
        if type(code) is not int:
            raise _ContractViolation(
                "INVALID_EXIT_CODE",
                f"value={code}",
            )
        if next_state["exit_code"] is not None:
            raise _ContractViolation(
                "DUPLICATE_EXIT",
                f"sequence={sequence}",
            )

        next_state["exit_code"] = code
        _record_terminal(
            next_state,
            "EXITED",
            sequence,
            "EXIT",
            "SUCCEEDED" if code == 0 else "FAILED",
            "STARTED",
        )

    elif event_type == "SILENCE":
        _require_exact_fields(
            event,
            {"type", "t"},
            {"type", "t"},
        )
        _event_time(event, record)
        _record_terminal(
            next_state,
            "SILENCE_TIMEOUT",
            sequence,
            "SILENCE",
            "FAILED",
            (
                "STARTED"
                if next_state["spawned"]
                else "MAY_HAVE_STARTED"
            ),
        )

    elif event_type == "PROCESS_DEADLINE":
        _require_exact_fields(
            event,
            {"type", "t"},
            {"type", "t"},
        )
        _event_time(event, record)
        _record_terminal(
            next_state,
            "PROCESS_TIMEOUT",
            sequence,
            "PROCESS_DEADLINE",
            "FAILED",
            (
                "STARTED"
                if next_state["spawned"]
                else "MAY_HAVE_STARTED"
            ),
        )

    elif event_type == "CANCEL_ACK":
        _require_exact_fields(
            event,
            {"type"},
            {"type"},
        )
        next_state["cancel_ack"] = True

    elif event_type == "TREE_STATE":
        _require_exact_fields(
            event,
            {"type", "identities"},
            {"type", "identities", "state"},
        )
        identities = event["identities"]
        if not isinstance(identities, list):
            raise _ContractViolation(
                "INVALID_TREE_STATE",
                "identities must be an array",
            )

        validated = [
            _validate_identity(identity)
            for identity in identities
        ]
        tokens = [
            identity["token"]
            for identity in validated
        ]
        if len(set(tokens)) != len(tokens):
            raise _ContractViolation(
                "INVALID_TREE_STATE",
                "identity tokens must be unique in a snapshot",
            )

        for identity in validated:
            token = identity["token"]
            prior = next_state["processes"].get(token)
            if prior is not None and prior != identity:
                raise _ContractViolation(
                    "PROCESS_IDENTITY_TOKEN_REUSED",
                    f"token={token}",
                )
            next_state["processes"][token] = identity

        next_state["tree_states"].append(
            {
                "identities": validated,
                "sequence": sequence,
                "state": copy.deepcopy(event.get("state")),
            }
        )

        if validated:
            next_state["spawned"] = True
            next_state["effect_certainty"] = "STARTED"
            if next_state["terminal_classification"] is None:
                next_state["execution_outcome"] = "RUNNING"

    elif event_type == "CLEANUP_ERROR":
        _require_exact_fields(
            event,
            {"type", "error"},
            {"type", "error"},
        )
        if event["error"] is None:
            raise _ContractViolation(
                "INVALID_CLEANUP_ERROR",
                "error cannot be null",
            )

        # Cleanup evidence is attached without touching the primary
        # terminal classification.
        next_state["cleanup_errors"].append(
            {
                "error": copy.deepcopy(event["error"]),
                "sequence": sequence,
            }
        )

    next_state["applied_event_ids"].append(event_id)
    next_state["last_sequence"] = sequence
    return next_state


def _append_journal(
    path: Path,
    record: dict[str, Any],
) -> None:
    raw = _canonical_json_bytes(record) + b"\n"
    with path.open("ab") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _read_journal_records(
    path: Path,
) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    if not raw:
        return []

    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        raw.splitlines(keepends=True),
        start=1,
    ):
        if not line.endswith(b"\n"):
            raise _ContractViolation(
                "TRUNCATED_JOURNAL_RECORD",
                f"line={line_number}",
            )

        payload = line[:-1]
        if not payload:
            raise _ContractViolation(
                "EMPTY_JOURNAL_RECORD",
                f"line={line_number}",
            )

        try:
            parsed = _parse_json_bytes(payload)
        except _ScriptParseError as exc:
            raise _ContractViolation(
                "JOURNAL_PARSE_ERROR",
                f"line={line_number};{exc.detail}",
            ) from exc

        if not isinstance(parsed, dict):
            raise _ContractViolation(
                "JOURNAL_RECORD_NOT_OBJECT",
                f"line={line_number}",
            )

        if _canonical_json_bytes(parsed) + b"\n" != line:
            raise _ContractViolation(
                "JOURNAL_RECORD_NOT_CANONICAL",
                f"line={line_number}",
            )

        records.append(parsed)

    return records


def _apply_records(
    records: list[dict[str, Any]],
    state: dict[str, Any],
) -> dict[str, Any]:
    reduced = copy.deepcopy(state)
    for record in records:
        reduced = _reduce_record(reduced, record)
    return reduced


def _recover_after_interruption(
    journal_path: Path,
    initial_state: dict[str, Any],
    interrupted_sequence: int,
) -> dict[str, Any]:
    records = _read_journal_records(journal_path)

    # Rebuild from the durable journal rather than trusting in-memory
    # state, then apply the same journal again to prove reducer
    # idempotence.
    recovered = _apply_records(records, initial_state)
    replayed = _apply_records(records, recovered)
    if (
        _canonical_json_bytes(recovered)
        != _canonical_json_bytes(replayed)
    ):
        raise RuntimeError("journal reducer is not idempotent")

    recovered["recovery"] = {
        "simulated_interruption": True,
        "interrupted_after_sequence": interrupted_sequence,
        "reducer_replay_idempotent": True,
        "external_dispatch_replayed": False,
        "confirmation_required": False,
    }

    # A durable intent or process observation without a durable terminal
    # receipt is uncertain. Recovery records that uncertainty and never
    # synthesizes or replays an external dispatch.
    if recovered["terminal_classification"] is None:
        _record_terminal(
            recovered,
            "START_UNCERTAIN",
            interrupted_sequence,
            "RECOVERY",
            "UNKNOWN",
            "MAY_HAVE_STARTED",
        )
        recovered["recovery"]["confirmation_required"] = True

    return recovered


def _evaluate_expectations(
    expect: dict[str, Any] | None,
    state: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    if expect is None:
        return {
            "declared": False,
            "passed": True,
            "failures": [],
        }

    allowed = {
        "terminal_classification",
        "exit_code",
        "ordered_event_types",
        "cleanup_error_count",
        "effect_certainty",
        "execution_outcome",
        "process_tokens",
        "cancel_ack",
        "stream_raw_sha256",
        "reducer_replay_idempotent",
    }
    unknown = sorted(set(expect) - allowed)
    if unknown:
        raise _ContractViolation(
            "UNKNOWN_EXPECTATION_FIELD",
            ",".join(unknown),
        )

    ordered_event_types = [
        record["event"].get("type")
        for record in records
    ]
    actual: dict[str, Any] = {
        "terminal_classification": (
            state["terminal_classification"]
        ),
        "exit_code": state["exit_code"],
        "ordered_event_types": ordered_event_types,
        "cleanup_error_count": len(state["cleanup_errors"]),
        "effect_certainty": state["effect_certainty"],
        "execution_outcome": state["execution_outcome"],
        "process_tokens": sorted(state["processes"]),
        "cancel_ack": state["cancel_ack"],
        "stream_raw_sha256": state["stream_raw_sha256"],
        "reducer_replay_idempotent": (
            state["recovery"]["reducer_replay_idempotent"]
        ),
    }

    expected = copy.deepcopy(expect)

    if "process_tokens" in expected:
        tokens = expected["process_tokens"]
        if (
            not isinstance(tokens, list)
            or not all(_valid_token(token) for token in tokens)
        ):
            raise _ContractViolation(
                "INVALID_EXPECTATION",
                (
                    "process_tokens must be an array of "
                    "stable tokens"
                ),
            )
        expected["process_tokens"] = sorted(tokens)

    if "ordered_event_types" in expected:
        event_types = expected["ordered_event_types"]
        if (
            not isinstance(event_types, list)
            or not all(
                isinstance(event_type, str)
                for event_type in event_types
            )
        ):
            raise _ContractViolation(
                "INVALID_EXPECTATION",
                "ordered_event_types must be a string array",
            )

    failures = [
        {
            "actual": actual[field],
            "expected": expected[field],
            "field": field,
        }
        for field in sorted(expected)
        if actual[field] != expected[field]
    ]

    return {
        "declared": True,
        "passed": not failures,
        "failures": failures,
    }


def _finish_fixture(
    root: Path,
    fixture_id: str,
    run_id: str | None,
    script_schema_version: int | None,
    input_event_count: int,
    state: dict[str, Any],
    status: str,
    diagnostics: list[dict[str, Any]],
    expectations: dict[str, Any],
    domain_verification: dict[str, Any] | None,
) -> Path:
    event_script_path = root / "event-script.json"
    journal_path = root / "journal.jsonl"
    state_before_path = root / "state-before.json"
    state_after_path = root / "state-after.json"
    transcript_path = root / "transcript.json"
    transcript_digest_path = root / "transcript.sha256"
    fixture_record_path = root / "fixture-record.json"

    records = _read_journal_records(journal_path)

    _write_new_json(state_after_path, state)

    transcript = {
        "schema_version": TRANSCRIPT_SCHEMA_VERSION,
        "runner_contract": CONTRACT_NAME,
        "fixture_id": fixture_id,
        "run_id": run_id,
        "events": records,
        "recovery": copy.deepcopy(state["recovery"]),
    }
    _write_new_json(transcript_path, transcript)

    transcript_sha256 = _sha256_file(transcript_path)
    _write_new_bytes(
        transcript_digest_path,
        (transcript_sha256 + "\n").encode("ascii"),
    )

    artifact_paths = {
        "event_script": "event-script.json",
        "journal": "journal.jsonl",
        "state_before": "state-before.json",
        "state_after": "state-after.json",
        "transcript": "transcript.json",
        "transcript_digest": "transcript.sha256",
        "fixture_record": "fixture-record.json",
    }

    digests = {
        "event_script_raw_sha256": _sha256_file(
            event_script_path
        ),
        "journal_raw_sha256": _sha256_file(journal_path),
        "state_before_raw_sha256": _sha256_file(
            state_before_path
        ),
        "state_after_raw_sha256": _sha256_file(
            state_after_path
        ),
        "transcript_raw_sha256": transcript_sha256,
    }

    if domain_verification is not None:
        artifact_paths.update(
            copy.deepcopy(
                domain_verification.get("artifact_paths", {})
            )
        )
        digests.update(
            copy.deepcopy(domain_verification.get("digests", {}))
        )

    fixture_record = {
        "schema_version": (
            DOMAIN_FIXTURE_RECORD_SCHEMA_VERSION
            if domain_verification is not None
            else FIXTURE_RECORD_SCHEMA_VERSION
        ),
        "runner_contract": CONTRACT_NAME,
        "script_schema_version": script_schema_version,
        "fixture_id": fixture_id,
        "run_id": run_id,
        "status": status,
        "artifact_paths": artifact_paths,
        "digests": digests,
        "terminal_classification": (
            state["terminal_classification"]
        ),
        "effect_certainty": state["effect_certainty"],
        "execution_outcome": state["execution_outcome"],
        "input_event_count": input_event_count,
        "journaled_event_count": len(records),
        "applied_event_count": len(
            state["applied_event_ids"]
        ),
        "cleanup_error_count": len(
            state["cleanup_errors"]
        ),
        "recovery": copy.deepcopy(state["recovery"]),
        "expectations": expectations,
        "diagnostics": diagnostics,
    }
    if domain_verification is not None:
        fixture_record["domain_verification"] = copy.deepcopy(
            domain_verification
        )
        if (
            status == "V1_CAPTURE"
            and domain_verification.get("status") == "PASS"
        ):
            fixture_record["coverage_scope"] = "SPEC_FAITHFUL"

    # The fixture record is deliberately written last. A partial or
    # crashed run therefore cannot leave a V1_CAPTURE claim behind.
    _write_new_json(fixture_record_path, fixture_record)
    return fixture_record_path


def run_fixture(
    event_script_path: str | Path,
    fixture_id: str,
    out_root: str | Path,
) -> Path:
    """Run one deterministic fixture and return its record path."""

    script_path = Path(event_script_path)
    root = Path(out_root)

    if not _FIXTURE_ID_RE.fullmatch(fixture_id):
        raise InvalidInvocationError(
            (
                "fixture ID must match "
                "[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
            )
        )

    if not script_path.is_file():
        raise InvalidInvocationError(
            "event script does not exist or is not a file"
        )

    if root.exists():
        raise InvalidInvocationError(
            "out-root already exists; a fresh root is required"
        )

    try:
        raw_script = script_path.read_bytes()
    except OSError as exc:
        raise InvalidInvocationError(
            "event script could not be read"
        ) from exc

    parsed: Any = None
    prepared: _PreparedScript | None = None
    preflight_status: str | None = None
    preflight_diagnostics: list[dict[str, Any]] = []

    try:
        parsed = _parse_json_bytes(raw_script)
    except _ScriptParseError as exc:
        preflight_status = "CONTRACT_VIOLATION"
        preflight_diagnostics.append(
            {
                "code": exc.code,
                "detail": exc.detail,
            }
        )
    else:
        try:
            prepared = _prepare_script(parsed)
        except _ContractViolation as exc:
            preflight_status = "CONTRACT_VIOLATION"
            preflight_diagnostics.append(
                {
                    "code": exc.code,
                    "detail": exc.detail,
                }
            )

    try:
        root.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise InvalidInvocationError(
            (
                "out-root was created concurrently; "
                "a fresh root is required"
            )
        ) from exc

    run_id = (
        prepared.run_id
        if prepared is not None
        else _run_id_hint(parsed)
    )
    input_event_count = (
        len(prepared.events)
        if prepared is not None
        else _input_event_count_hint(parsed)
    )

    initial_state = _initial_state(fixture_id, run_id)
    state = copy.deepcopy(initial_state)

    # The original raw script is copied into the isolated evidence root.
    # Subsequent processing reads only that root.
    _write_new_bytes(
        root / "event-script.json",
        raw_script,
    )
    _write_new_bytes(
        root / "journal.jsonl",
        b"",
    )
    _write_new_json(
        root / "state-before.json",
        initial_state,
    )

    default_expectations = {
        "declared": False,
        "passed": False,
        "failures": [],
    }

    if prepared is None:
        return _finish_fixture(
            root=root,
            fixture_id=fixture_id,
            run_id=run_id,
            script_schema_version=None,
            input_event_count=input_event_count,
            state=state,
            status=preflight_status or "RUNNER_ERROR",
            diagnostics=preflight_diagnostics,
            expectations=default_expectations,
            domain_verification=None,
        )

    journal_path = root / "journal.jsonl"
    status: str | None = None
    diagnostics: list[dict[str, Any]] = []
    expectations = default_expectations
    domain_required = DOMAIN_REGISTRY.requires_verification(fixture_id)
    domain_declared = prepared.domain_case is not None
    domain_verification: dict[str, Any] | None = None

    try:
        for sequence, event in enumerate(prepared.events):
            record = {
                "record_type": "INPUT_EVENT",
                "sequence": sequence,
                "event_id": prepared.ids[sequence + 1],
                "observed_monotonic": (
                    prepared.clock[sequence]
                ),
                "event": copy.deepcopy(event),
            }

            # Durability precedes event validation and all state
            # reduction.
            _append_journal(journal_path, record)

            try:
                if (
                    prepared.interrupt_after_append
                    == sequence
                ):
                    state = _recover_after_interruption(
                        journal_path,
                        initial_state,
                        sequence,
                    )
                else:
                    state = _reduce_record(state, record)

            except _UnsupportedEvent as exc:
                status = "UNSUPPORTED_EVENT"
                diagnostics.append(
                    {
                        "code": "UNSUPPORTED_EVENT",
                        "detail": (
                            f"event_type={exc.event_type};"
                            f"sequence={sequence}"
                        ),
                    }
                )
                break

            except _ContractViolation as exc:
                status = "CONTRACT_VIOLATION"
                diagnostics.append(
                    {
                        "code": exc.code,
                        "detail": (
                            f"sequence={sequence};"
                            f"{exc.detail}"
                        ),
                    }
                )
                break

        if (
            status is None
            and state["terminal_classification"] is None
        ):
            status = "CONTRACT_VIOLATION"
            diagnostics.append(
                {
                    "code": (
                        "MISSING_TERMINAL_CLASSIFICATION"
                    ),
                    "detail": (
                        "a completed fixture script must "
                        "produce a terminal result"
                    ),
                }
            )

        if status is None:
            records = _read_journal_records(journal_path)
            try:
                expectations = _evaluate_expectations(
                    prepared.expect,
                    state,
                    records,
                )
            except _ContractViolation as exc:
                status = "CONTRACT_VIOLATION"
                diagnostics.append(
                    {
                        "code": exc.code,
                        "detail": exc.detail,
                    }
                )
            else:
                if not expectations["passed"]:
                    status = "ASSERTION_FAILED"
                elif prepared.domain_case is None:
                    if domain_required:
                        status = "DOMAIN_VERIFICATION_REQUIRED"
                        domain_verification = {
                            "declared": False,
                            "required": True,
                            "status": "MISSING",
                            "fixture_id": fixture_id,
                            "artifact_paths": {},
                            "digests": {},
                        }
                        diagnostics.append(
                            {
                                "code": "DOMAIN_VERIFICATION_REQUIRED",
                                "detail": f"fixture_id={fixture_id}",
                            }
                        )
                    else:
                        status = "V1_CAPTURE"
                else:
                    try:
                        domain_result = DOMAIN_REGISTRY.verify(
                            prepared.domain_case,
                            fixture_id,
                            IsolatedDomainContext(
                                root=root,
                                clock=prepared.clock,
                                ids=prepared.ids,
                            ),
                        )
                        domain_verification = write_domain_artifacts(
                            root,
                            domain_result,
                        )
                    except DomainContractError as exc:
                        status = "CONTRACT_VIOLATION"
                        domain_verification = {
                            "declared": True,
                            "required": domain_required,
                            "status": "ERROR",
                            "fixture_id": fixture_id,
                            "error": {
                                "code": exc.code,
                                "detail": exc.detail,
                            },
                            "artifact_paths": {},
                            "digests": {},
                        }
                        diagnostics.append(
                            {
                                "code": exc.code,
                                "detail": exc.detail,
                            }
                        )
                    else:
                        if domain_result.passed:
                            status = "V1_CAPTURE"
                        else:
                            status = "DOMAIN_ASSERTION_FAILED"
                            diagnostics.append(
                                {
                                    "code": "DOMAIN_ASSERTION_FAILED",
                                    "detail": f"fixture_id={fixture_id}",
                                }
                            )

    except Exception as exc:
        # An unexpected but containable failure produces an explicit
        # error record. Failures that prevent artifact finalization
        # propagate to the CLI as a genuine runner crash.
        status = "RUNNER_ERROR"
        diagnostics.append(
            {
                "code": "INTERNAL_EXCEPTION",
                "detail": (
                    f"exception_type={type(exc).__name__}"
                ),
            }
        )

    if (
        domain_verification is None
        and (domain_required or domain_declared)
    ):
        domain_verification = {
            "declared": domain_declared,
            "required": domain_required,
            "status": "SKIPPED_CORE_GATE_FAILED",
            "fixture_id": fixture_id,
            "artifact_paths": {},
            "digests": {},
        }

    return _finish_fixture(
        root=root,
        fixture_id=fixture_id,
        run_id=run_id,
        script_schema_version=prepared.schema_version,
        input_event_count=input_event_count,
        state=state,
        status=status or "RUNNER_ERROR",
        diagnostics=diagnostics,
        expectations=expectations,
        domain_verification=domain_verification,
    )
