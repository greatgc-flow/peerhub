from __future__ import annotations

import base64
import binascii
import codecs
import hashlib
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
    "DT-02",
    "DT-03",
    "DT-04",
    "DT-05",
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "DT-02": "transport.dt02.incremental_framing",
    "DT-03": "transport.dt03.independent_timeouts",
    "DT-04": "transport.dt04.cancellation_ladder",
    "DT-05": "transport.dt05.tree_closure",
}


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


def _canonical_base64(
    value: Any,
    path: str,
) -> str:
    text = require_string(value, path)

    try:
        raw = base64.b64decode(
            text.encode("ascii"),
            validate=True,
        )
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} must be canonical base64",
        ) from exc

    if base64.b64encode(raw).decode("ascii") != text:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} must use canonical base64 padding",
        )

    return text


def validate_transport_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one closed transport input schema."""

    base = _base_fixture_id(fixture_id)
    inputs = require_mapping(raw_inputs, "inputs")

    if base == "DT-02":
        require_exact_fields(
            inputs,
            {"chunks_base64"},
            path="inputs",
        )

        chunks = inputs["chunks_base64"]
        if not isinstance(chunks, list) or not chunks:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "inputs.chunks_base64 must be "
                    "a non-empty array"
                ),
            )

        validated_chunks = [
            _canonical_base64(
                value,
                f"inputs.chunks_base64[{index}]",
            )
            for index, value in enumerate(chunks)
        ]

        raw = b"".join(
            base64.b64decode(
                value.encode("ascii"),
                validate=True,
            )
            for value in validated_chunks
        )

        try:
            raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "inputs.chunks_base64 "
                    f"invalid_utf8_at_byte={exc.start}"
                ),
            ) from exc

        return {
            "chunks_base64": validated_chunks,
        }

    if base == "DT-03":
        require_exact_fields(
            inputs,
            {"timelines"},
            path="inputs",
        )

        timelines = inputs["timelines"]
        if (
            not isinstance(timelines, list)
            or len(timelines) != 2
        ):
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "inputs.timelines must contain "
                    "exactly two timelines"
                ),
            )

        validated_timelines: list[
            dict[str, Any]
        ] = []
        timeline_ids: set[str] = set()
        terminal_kinds: set[str] = set()

        for timeline_index, raw_timeline in enumerate(
            timelines
        ):
            timeline = require_mapping(
                raw_timeline,
                f"inputs.timelines[{timeline_index}]",
            )
            require_exact_fields(
                timeline,
                {"timeline_id", "events"},
                path=(
                    f"inputs.timelines[{timeline_index}]"
                ),
            )

            timeline_id = require_string(
                timeline["timeline_id"],
                (
                    f"inputs.timelines[{timeline_index}]"
                    ".timeline_id"
                ),
            )
            if timeline_id in timeline_ids:
                raise DomainContractError(
                    "DOMAIN_INPUT_INVALID",
                    f"duplicate timeline_id={timeline_id}",
                )
            timeline_ids.add(timeline_id)

            events = timeline["events"]
            if not isinstance(events, list) or not events:
                raise DomainContractError(
                    "DOMAIN_INPUT_INVALID",
                    (
                        f"timeline_id={timeline_id} "
                        "must contain events"
                    ),
                )

            validated_events: list[
                dict[str, Any]
            ] = []
            previous_t: int | None = None
            terminal_events: list[str] = []

            for event_index, raw_event in enumerate(events):
                event_path = (
                    f"inputs.timelines[{timeline_index}]"
                    f".events[{event_index}]"
                )
                event = require_mapping(
                    raw_event,
                    event_path,
                )
                require_exact_fields(
                    event,
                    {"type", "t"},
                    path=event_path,
                )

                event_type = require_string(
                    event["type"],
                    f"{event_path}.type",
                )
                if event_type not in {
                    "OUTPUT_OBSERVED",
                    "SILENCE",
                    "PROCESS_DEADLINE",
                }:
                    raise DomainContractError(
                        "DOMAIN_INPUT_INVALID",
                        (
                            "unsupported timeline "
                            f"event={event_type}"
                        ),
                    )

                tick = require_nonnegative_int(
                    event["t"],
                    f"{event_path}.t",
                )
                if (
                    previous_t is not None
                    and tick < previous_t
                ):
                    raise DomainContractError(
                        "DOMAIN_INPUT_INVALID",
                        (
                            f"timeline_id={timeline_id} "
                            "is non-monotonic"
                        ),
                    )
                previous_t = tick

                if event_type in {
                    "SILENCE",
                    "PROCESS_DEADLINE",
                }:
                    terminal_events.append(event_type)

                validated_events.append(
                    {
                        "type": event_type,
                        "t": tick,
                    }
                )

            if len(terminal_events) != 1:
                raise DomainContractError(
                    "DOMAIN_INPUT_INVALID",
                    (
                        f"timeline_id={timeline_id} "
                        "requires one timeout event"
                    ),
                )

            terminal_kinds.add(terminal_events[0])
            validated_timelines.append(
                {
                    "timeline_id": timeline_id,
                    "events": validated_events,
                }
            )

        if terminal_kinds != {
            "SILENCE",
            "PROCESS_DEADLINE",
        }:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "timelines must independently "
                    "cover both timeout kinds"
                ),
            )

        return {
            "timelines": validated_timelines,
        }

    if base == "DT-04":
        fields = {
            "spawn_observed",
            "soft_cancel_acknowledged",
            "terminate_acknowledged",
            "kill_acknowledged",
            "tree_closed",
        }
        require_exact_fields(
            inputs,
            fields,
            path="inputs",
        )

        return {
            field: require_bool(
                inputs[field],
                f"inputs.{field}",
            )
            for field in sorted(fields)
        }

    require_exact_fields(
        inputs,
        {
            "initial_identities",
            "post_cancellation_observations",
        },
        path="inputs",
    )

    identities = inputs["initial_identities"]
    if not isinstance(identities, list) or not identities:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "inputs.initial_identities must be "
                "a non-empty array"
            ),
        )

    validated_identities = [
        require_string(
            token,
            f"inputs.initial_identities[{index}]",
        )
        for index, token in enumerate(identities)
    ]
    if (
        len(set(validated_identities))
        != len(validated_identities)
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            "initial identity tokens must be unique",
        )

    observations = inputs[
        "post_cancellation_observations"
    ]
    if not isinstance(observations, list):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            (
                "inputs.post_cancellation_observations "
                "must be an array"
            ),
        )

    validated_observations: list[
        dict[str, str]
    ] = []
    observed_tokens: set[str] = set()
    allowed_states = {
        "TERMINATED",
        "RUNNING",
        "IDENTITY_UNCERTAIN",
    }

    for index, raw_observation in enumerate(
        observations
    ):
        observation_path = (
            "inputs.post_cancellation_observations"
            f"[{index}]"
        )
        observation = require_mapping(
            raw_observation,
            observation_path,
        )
        require_exact_fields(
            observation,
            {"token", "state"},
            path=observation_path,
        )

        token = require_string(
            observation["token"],
            f"{observation_path}.token",
        )
        state = require_string(
            observation["state"],
            f"{observation_path}.state",
        )

        if token not in validated_identities:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    "observation token was not in "
                    f"initial tree: {token}"
                ),
            )

        if token in observed_tokens:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                f"duplicate observation token={token}",
            )

        if state not in allowed_states:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                f"unsupported identity state={state}",
            )

        observed_tokens.add(token)
        validated_observations.append(
            {
                "token": token,
                "state": state,
            }
        )

    return {
        "initial_identities": validated_identities,
        "post_cancellation_observations": (
            validated_observations
        ),
    }


def validate_transport_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the oracle and subject output schema."""

    base = _base_fixture_id(fixture_id)
    output = require_mapping(raw_output, "output")

    if base == "DT-02":
        require_exact_fields(
            output,
            {
                "status",
                "canonical_text",
                "lines",
                "stream_raw_sha256",
            },
            path="output",
        )

        status = require_string(
            output["status"],
            "output.status",
        )
        canonical_text = output["canonical_text"]
        lines = output["lines"]
        digest = output["stream_raw_sha256"]

        if (
            status != "OK"
            or not isinstance(canonical_text, str)
        ):
            raise DomainContractError(
                "DOMAIN_OUTPUT_INVALID",
                "DT-02 status/text shape is invalid",
            )

        if (
            not isinstance(lines, list)
            or not all(
                isinstance(line, str)
                for line in lines
            )
        ):
            raise DomainContractError(
                "DOMAIN_OUTPUT_INVALID",
                (
                    "DT-02 lines must be "
                    "a string array"
                ),
            )

        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in digest
            )
        ):
            raise DomainContractError(
                "DOMAIN_OUTPUT_INVALID",
                "DT-02 stream digest is invalid",
            )

        return {
            "status": status,
            "canonical_text": canonical_text,
            "lines": list(lines),
            "stream_raw_sha256": digest,
        }

    if base == "DT-03":
        require_exact_fields(
            output,
            {"classifications"},
            path="output",
        )

        classifications = output["classifications"]
        if (
            not isinstance(classifications, list)
            or len(classifications) != 2
        ):
            raise DomainContractError(
                "DOMAIN_OUTPUT_INVALID",
                "DT-03 requires two classifications",
            )

        validated: list[dict[str, str]] = []
        seen: set[str] = set()

        for index, raw_item in enumerate(
            classifications
        ):
            item_path = (
                f"output.classifications[{index}]"
            )
            item = require_mapping(
                raw_item,
                item_path,
            )
            require_exact_fields(
                item,
                {"timeline_id", "terminal"},
                path=item_path,
            )

            timeline_id = require_string(
                item["timeline_id"],
                f"{item_path}.timeline_id",
            )
            terminal = require_string(
                item["terminal"],
                f"{item_path}.terminal",
            )

            if (
                timeline_id in seen
                or terminal not in {
                    "SILENCE_TIMEOUT",
                    "PROCESS_TIMEOUT",
                }
            ):
                raise DomainContractError(
                    "DOMAIN_OUTPUT_INVALID",
                    "DT-03 classification is invalid",
                )

            seen.add(timeline_id)
            validated.append(
                {
                    "timeline_id": timeline_id,
                    "terminal": terminal,
                }
            )

        return {
            "classifications": sorted(
                validated,
                key=lambda item: item["timeline_id"],
            )
        }

    if base == "DT-04":
        require_exact_fields(
            output,
            {
                "steps",
                "terminal",
                "effect_certainty",
                "cleanup_reconciled",
            },
            path="output",
        )

        steps = output["steps"]
        if (
            not isinstance(steps, list)
            or not all(
                isinstance(step, str)
                for step in steps
            )
        ):
            raise DomainContractError(
                "DOMAIN_OUTPUT_INVALID",
                (
                    "DT-04 steps must be "
                    "a string array"
                ),
            )

        terminal = require_string(
            output["terminal"],
            "output.terminal",
        )
        certainty = require_string(
            output["effect_certainty"],
            "output.effect_certainty",
        )
        reconciled = require_bool(
            output["cleanup_reconciled"],
            "output.cleanup_reconciled",
        )

        if (
            terminal != "PROCESS_TIMEOUT"
            or certainty not in {
                "STARTED",
                "MAY_HAVE_STARTED",
            }
        ):
            raise DomainContractError(
                "DOMAIN_OUTPUT_INVALID",
                (
                    "DT-04 terminal/certainty "
                    "is invalid"
                ),
            )

        return {
            "steps": list(steps),
            "terminal": terminal,
            "effect_certainty": certainty,
            "cleanup_reconciled": reconciled,
        }

    require_exact_fields(
        output,
        {
            "terminated_tokens",
            "unresolved_tokens",
            "all_initial_identities_accounted_for",
            "cleanup_complete",
            "cleanup_classification",
        },
        path="output",
    )

    terminated = output["terminated_tokens"]
    unresolved = output["unresolved_tokens"]

    if (
        not isinstance(terminated, list)
        or not all(
            isinstance(token, str)
            for token in terminated
        )
    ):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "DT-05 terminated_tokens must be "
                "a string array"
            ),
        )

    if (
        not isinstance(unresolved, list)
        or not all(
            isinstance(token, str)
            for token in unresolved
        )
    ):
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "DT-05 unresolved_tokens must be "
                "a string array"
            ),
        )

    accounted = require_bool(
        output[
            "all_initial_identities_accounted_for"
        ],
        (
            "output."
            "all_initial_identities_accounted_for"
        ),
    )
    complete = require_bool(
        output["cleanup_complete"],
        "output.cleanup_complete",
    )
    classification = output[
        "cleanup_classification"
    ]

    if classification not in {
        None,
        "CANCELLATION_CLEANUP_FAILED",
    }:
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID",
            (
                "DT-05 cleanup classification "
                "is invalid"
            ),
        )

    return {
        "terminated_tokens": sorted(terminated),
        "unresolved_tokens": sorted(unresolved),
        "all_initial_identities_accounted_for": (
            accounted
        ),
        "cleanup_complete": complete,
        "cleanup_classification": classification,
    }


class TransportOracle:
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
        if _base_fixture_id(fixture_id) != self._base:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                (
                    f"oracle_id={self.oracle_id};"
                    f"fixture_id={fixture_id}"
                ),
            )

        if self._base == "DT-02":
            return self._dt02(raw_inputs)
        if self._base == "DT-03":
            return self._dt03(raw_inputs)
        if self._base == "DT-04":
            return self._dt04(raw_inputs)

        return self._dt05(raw_inputs)

    def _dt02(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        raw = b"".join(
            base64.b64decode(
                value.encode("ascii"),
                validate=True,
            )
            for value in inputs["chunks_base64"]
        )
        text = raw.decode(
            "utf-8",
            errors="strict",
        )
        canonical_text = (
            text.replace("\r\n", "\n")
            .replace("\r", "\n")
        )

        lines = canonical_text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        if canonical_text == "":
            lines = []

        return {
            "status": "OK",
            "canonical_text": canonical_text,
            "lines": lines,
            "stream_raw_sha256": (
                hashlib.sha256(raw).hexdigest()
            ),
        }

    def _dt03(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        classifications: list[
            dict[str, str]
        ] = []

        for timeline in inputs["timelines"]:
            timeout_type = next(
                event["type"]
                for event in timeline["events"]
                if event["type"] in {
                    "SILENCE",
                    "PROCESS_DEADLINE",
                }
            )
            classifications.append(
                {
                    "timeline_id": (
                        timeline["timeline_id"]
                    ),
                    "terminal": (
                        "SILENCE_TIMEOUT"
                        if timeout_type == "SILENCE"
                        else "PROCESS_TIMEOUT"
                    ),
                }
            )

        return {
            "classifications": sorted(
                classifications,
                key=lambda item: item["timeline_id"],
            )
        }

    def _dt04(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        steps = [
            "PROCESS_DEADLINE",
            "SOFT_CANCEL",
        ]

        if not inputs[
            "soft_cancel_acknowledged"
        ]:
            steps.append("TERMINATE_TREE")
            if not inputs[
                "terminate_acknowledged"
            ]:
                steps.append("KILL_TREE")

        steps.append("RECONCILE_TREE")
        reconciled = bool(inputs["tree_closed"])

        return {
            "steps": steps,
            "terminal": "PROCESS_TIMEOUT",
            "effect_certainty": (
                "STARTED"
                if (
                    inputs["spawn_observed"]
                    and reconciled
                )
                else "MAY_HAVE_STARTED"
            ),
            "cleanup_reconciled": reconciled,
        }

    def _dt05(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        states = {
            observation["token"]: (
                observation["state"]
            )
            for observation in inputs[
                "post_cancellation_observations"
            ]
        }

        terminated = sorted(
            token
            for token in inputs[
                "initial_identities"
            ]
            if states.get(token) == "TERMINATED"
        )
        unresolved = sorted(
            token
            for token in inputs[
                "initial_identities"
            ]
            if states.get(token) != "TERMINATED"
        )
        complete = not unresolved

        return {
            "terminated_tokens": terminated,
            "unresolved_tokens": unresolved,
            "all_initial_identities_accounted_for": (
                sorted(terminated + unresolved)
                == sorted(
                    inputs["initial_identities"]
                )
            ),
            "cleanup_complete": complete,
            "cleanup_classification": (
                None
                if complete
                else "CANCELLATION_CLEANUP_FAILED"
            ),
        }


class TransportSubjectAdapter:
    adapter_version = 1

    def __init__(
        self,
        base_fixture_id: str,
    ) -> None:
        self._base = base_fixture_id
        self.adapter_id = (
            "transport."
            f"{base_fixture_id.lower()}."
            "reference"
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

        if self._base == "DT-02":
            return self._dt02(raw_inputs)
        if self._base == "DT-03":
            return self._dt03(raw_inputs)
        if self._base == "DT-04":
            return self._dt04(raw_inputs)

        return self._dt05(raw_inputs)

    def _dt02(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        decoder = codecs.getincrementaldecoder(
            "utf-8"
        )(errors="strict")

        canonical_characters: list[str] = []
        pending_cr = False
        raw_hasher = hashlib.sha256()

        for encoded in inputs["chunks_base64"]:
            chunk = base64.b64decode(
                encoded.encode("ascii"),
                validate=True,
            )
            raw_hasher.update(chunk)
            decoded = decoder.decode(
                chunk,
                final=False,
            )

            for character in decoded:
                if pending_cr:
                    canonical_characters.append("\n")
                    pending_cr = False
                    if character == "\n":
                        continue

                if character == "\r":
                    pending_cr = True
                else:
                    canonical_characters.append(
                        character
                    )

        tail = decoder.decode(b"", final=True)
        for character in tail:
            if pending_cr:
                canonical_characters.append("\n")
                pending_cr = False
                if character == "\n":
                    continue

            if character == "\r":
                pending_cr = True
            else:
                canonical_characters.append(character)

        if pending_cr:
            canonical_characters.append("\n")

        canonical_text = "".join(
            canonical_characters
        )

        lines: list[str] = []
        current: list[str] = []
        for character in canonical_text:
            if character == "\n":
                lines.append("".join(current))
                current = []
            else:
                current.append(character)

        if current:
            lines.append("".join(current))

        return {
            "status": "OK",
            "canonical_text": canonical_text,
            "lines": lines,
            "stream_raw_sha256": (
                raw_hasher.hexdigest()
            ),
        }

    def _dt03(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        rows: list[dict[str, str]] = []

        for timeline in inputs["timelines"]:
            terminal: str | None = None

            for event in timeline["events"]:
                if (
                    terminal is None
                    and event["type"] == "SILENCE"
                ):
                    terminal = "SILENCE_TIMEOUT"
                elif (
                    terminal is None
                    and event["type"]
                    == "PROCESS_DEADLINE"
                ):
                    terminal = "PROCESS_TIMEOUT"

            if terminal is None:
                raise DomainContractError(
                    "DOMAIN_ADAPTER_ERROR",
                    (
                        "timeline_id="
                        f"{timeline['timeline_id']} "
                        "has no timeout"
                    ),
                )

            rows.append(
                {
                    "timeline_id": (
                        timeline["timeline_id"]
                    ),
                    "terminal": terminal,
                }
            )

        rows.sort(
            key=lambda item: item["timeline_id"]
        )
        return {
            "classifications": rows,
        }

    def _dt04(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        actions = ["PROCESS_DEADLINE"]
        stage = "SOFT_CANCEL"
        actions.append(stage)

        if not inputs[
            "soft_cancel_acknowledged"
        ]:
            stage = "TERMINATE_TREE"
            actions.append(stage)

            if not inputs[
                "terminate_acknowledged"
            ]:
                stage = "KILL_TREE"
                actions.append(stage)

        actions.append("RECONCILE_TREE")

        cleanup_reconciled = (
            inputs["tree_closed"] is True
        )
        certainty = "MAY_HAVE_STARTED"
        if (
            inputs["spawn_observed"]
            and cleanup_reconciled
        ):
            certainty = "STARTED"

        return {
            "steps": actions,
            "terminal": "PROCESS_TIMEOUT",
            "effect_certainty": certainty,
            "cleanup_reconciled": (
                cleanup_reconciled
            ),
        }

    def _dt05(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        terminated_tokens: list[str] = []
        unresolved_tokens: list[str] = []

        observations = {
            item["token"]: item["state"]
            for item in inputs[
                "post_cancellation_observations"
            ]
        }

        for token in inputs[
            "initial_identities"
        ]:
            if (
                observations.get(token)
                == "TERMINATED"
            ):
                terminated_tokens.append(token)
            else:
                unresolved_tokens.append(token)

        terminated_tokens.sort()
        unresolved_tokens.sort()

        accounted = (
            set(terminated_tokens).union(
                unresolved_tokens
            )
            == set(inputs["initial_identities"])
        )
        cleanup_complete = (
            len(unresolved_tokens) == 0
        )
        classification: str | None = None
        if not cleanup_complete:
            classification = (
                "CANCELLATION_CLEANUP_FAILED"
            )

        return {
            "terminated_tokens": terminated_tokens,
            "unresolved_tokens": unresolved_tokens,
            "all_initial_identities_accounted_for": (
                accounted
            ),
            "cleanup_complete": cleanup_complete,
            "cleanup_classification": (
                classification
            ),
        }


class FaultInjectedTransportAdapter:
    adapter_version = 1

    def __init__(
        self,
        base_fixture_id: str,
    ) -> None:
        self._base = base_fixture_id
        self.adapter_id = (
            "transport."
            f"{base_fixture_id.lower()}."
            "fault-injected"
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

        if self._base == "DT-02":
            return self._dt02(raw_inputs)
        if self._base == "DT-03":
            return self._dt03(raw_inputs)
        if self._base == "DT-04":
            return self._dt04(raw_inputs)

        return self._dt05(raw_inputs)

    def _dt02(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        raw = b"".join(
            base64.b64decode(
                value.encode("ascii"),
                validate=True,
            )
            for value in inputs["chunks_base64"]
        )

        incorrectly_decoded = "".join(
            base64.b64decode(
                value.encode("ascii"),
                validate=True,
            ).decode(
                "utf-8",
                errors="replace",
            )
            for value in inputs["chunks_base64"]
        )

        canonical_text = (
            incorrectly_decoded
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )
        lines = canonical_text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()

        return {
            "status": "OK",
            "canonical_text": canonical_text,
            "lines": lines,
            "stream_raw_sha256": (
                hashlib.sha256(raw).hexdigest()
            ),
        }

    def _dt03(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        first_terminal = next(
            event["type"]
            for event in inputs[
                "timelines"
            ][0]["events"]
            if event["type"] in {
                "SILENCE",
                "PROCESS_DEADLINE",
            }
        )
        leaked = (
            "SILENCE_TIMEOUT"
            if first_terminal == "SILENCE"
            else "PROCESS_TIMEOUT"
        )

        rows = [
            {
                "timeline_id": (
                    timeline["timeline_id"]
                ),
                "terminal": leaked,
            }
            for timeline in inputs["timelines"]
        ]

        return {
            "classifications": sorted(
                rows,
                key=lambda item: item["timeline_id"],
            )
        }

    def _dt04(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "steps": [
                "PROCESS_DEADLINE",
                "SOFT_CANCEL",
                "TERMINATE_TREE",
                "KILL_TREE",
            ],
            "terminal": "PROCESS_TIMEOUT",
            "effect_certainty": "STARTED",
            "cleanup_reconciled": (
                inputs["tree_closed"]
            ),
        }

    def _dt05(
        self,
        inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        all_tokens = sorted(
            inputs["initial_identities"]
        )

        return {
            "terminated_tokens": all_tokens,
            "unresolved_tokens": [],
            "all_initial_identities_accounted_for": (
                True
            ),
            "cleanup_complete": True,
            "cleanup_classification": None,
        }


def transport_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return immutable built-in DT registry rows."""

    registrations: list[
        DomainRegistration
    ] = []

    for base_fixture_id in _BASE_FIXTURES:
        oracle = TransportOracle(
            base_fixture_id
        )
        positive_adapter = (
            TransportSubjectAdapter(
                base_fixture_id
            )
        )
        negative_adapter = (
            FaultInjectedTransportAdapter(
                base_fixture_id
            )
        )

        registrations.append(
            DomainRegistration(
                fixture_id=base_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=positive_adapter,
                input_validator=(
                    validate_transport_inputs
                ),
                output_validator=(
                    validate_transport_output
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
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=negative_adapter,
                input_validator=(
                    validate_transport_inputs
                ),
                output_validator=(
                    validate_transport_output
                ),
            )
        )

    return tuple(registrations)