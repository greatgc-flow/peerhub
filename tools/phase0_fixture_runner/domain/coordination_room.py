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
    "CR-01",
    "CR-02",
    "CR-03",
    "CR-04",
    "CR-05",
    "CR-06",
)
_NEGATIVE_SUFFIX = "-NEG-01"

_ORACLE_IDS = {
    "CR-01": "coordination_room.cr01.session_close",
    "CR-02": "coordination_room.cr02.direct_read",
    "CR-03": "coordination_room.cr03.broadcast_order",
    "CR-04": "coordination_room.cr04.checkpoint_source_reference",
    "CR-05": "coordination_room.cr05.owner_heartbeat",
    "CR-06": "coordination_room.cr06.serialized_retirement",
}

_CASE_KINDS = frozenset(
    {
        "SESSION_CLOSE",
        "DIRECT_READ",
        "BROADCAST",
        "CHECKPOINT",
        "OWNER_HEARTBEAT",
        "SERIALIZED_RETIRE_SET",
    }
)
_RULE_TIERS = frozenset({"OBS", "CANDIDATE"})
_DECISIONS = frozenset(
    {
        "SESSION_CLOSED",
        "MESSAGE_READ",
        "BROADCAST_DELIVERED",
        "CHECKPOINT_RECORDED",
        "HEARTBEAT_ACCEPTED",
        "HEARTBEAT_REJECTED",
        "SERIALIZED_TRANSITION",
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


def _require_string_list(
    value: Any,
    path: str,
) -> list[str]:
    if not isinstance(value, list) or not value:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} must be a non-empty array",
        )
    values = [
        require_string(item, f"{path}[{index}]")
        for index, item in enumerate(value)
    ]
    if len(values) != len(set(values)):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} contains duplicate values",
        )
    return values


def _require_positive_int_list(
    value: Any,
    path: str,
) -> list[int]:
    if not isinstance(value, list) or not value:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} must be a non-empty array",
        )
    values = [
        require_nonnegative_int(item, f"{path}[{index}]")
        for index, item in enumerate(value)
    ]
    if any(item == 0 for item in values):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} values must be positive",
        )
    if len(values) != len(set(values)):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} contains duplicate values",
        )
    return values


def _validate_source_reference(
    value: Any,
    path: str,
    *,
    nullable: bool = False,
    output: bool = False,
) -> dict[str, str] | None:
    if value is None and nullable:
        return None

    reference = require_mapping(value, path)
    require_exact_fields(
        reference,
        {"reference_type", "reference_value"},
        path=path,
    )
    reference_type = require_string(
        reference["reference_type"],
        f"{path}.reference_type",
    )
    if reference_type != "SOURCE_DIGEST":
        raise DomainContractError(
            "DOMAIN_OUTPUT_INVALID" if output else "DOMAIN_INPUT_INVALID",
            f"{path}.reference_type unsupported={reference_type}",
        )
    return {
        "reference_type": reference_type,
        "reference_value": require_string(
            reference["reference_value"],
            f"{path}.reference_value",
        ),
    }


def _validate_session_close_facts(
    value: Any,
    path: str,
) -> dict[str, Any]:
    facts = require_mapping(value, path)
    require_exact_fields(
        facts,
        {
            "session_id",
            "members",
            "state_before_digest",
            "state_after_digest",
            "close_exit_code",
        },
        path=path,
    )
    close_exit_code = require_nonnegative_int(
        facts["close_exit_code"],
        f"{path}.close_exit_code",
    )
    if close_exit_code != 0:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path}.close_exit_code must be 0",
        )
    before = require_string(
        facts["state_before_digest"],
        f"{path}.state_before_digest",
    )
    after = require_string(
        facts["state_after_digest"],
        f"{path}.state_after_digest",
    )
    if before == after:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} requires a distinct post-close state digest",
        )
    return {
        "session_id": require_string(
            facts["session_id"],
            f"{path}.session_id",
        ),
        "members": _require_string_list(
            facts["members"],
            f"{path}.members",
        ),
        "state_before_digest": before,
        "state_after_digest": after,
        "close_exit_code": close_exit_code,
    }


def _validate_direct_read_facts(
    value: Any,
    path: str,
) -> dict[str, Any]:
    facts = require_mapping(value, path)
    require_exact_fields(
        facts,
        {
            "message_id",
            "thread_id",
            "sender",
            "recipient",
            "initial_status",
            "mark_read_requests",
        },
        path=path,
    )
    message_id = require_nonnegative_int(
        facts["message_id"],
        f"{path}.message_id",
    )
    if message_id == 0:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path}.message_id must be positive",
        )
    sender = require_string(facts["sender"], f"{path}.sender")
    recipient = require_string(
        facts["recipient"],
        f"{path}.recipient",
    )
    if sender == recipient:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path}.sender and recipient must differ",
        )
    if facts["initial_status"] != "UNREAD":
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path}.initial_status must be UNREAD",
        )
    requests = require_nonnegative_int(
        facts["mark_read_requests"],
        f"{path}.mark_read_requests",
    )
    if requests != 1:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path}.mark_read_requests must be exactly 1",
        )
    return {
        "message_id": message_id,
        "thread_id": require_string(
            facts["thread_id"],
            f"{path}.thread_id",
        ),
        "sender": sender,
        "recipient": recipient,
        "initial_status": "UNREAD",
        "mark_read_requests": requests,
    }


def _validate_broadcast_facts(
    value: Any,
    path: str,
) -> dict[str, Any]:
    facts = require_mapping(value, path)
    require_exact_fields(
        facts,
        {"thread_id", "recipient_order", "message_ids"},
        path=path,
    )
    recipients = _require_string_list(
        facts["recipient_order"],
        f"{path}.recipient_order",
    )
    message_ids = _require_positive_int_list(
        facts["message_ids"],
        f"{path}.message_ids",
    )
    if len(recipients) < 2 or len(recipients) != len(message_ids):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} requires paired broadcast recipients and message IDs",
        )
    return {
        "thread_id": require_string(
            facts["thread_id"],
            f"{path}.thread_id",
        ),
        "recipient_order": recipients,
        "message_ids": message_ids,
    }


def _validate_checkpoint_facts(
    value: Any,
    path: str,
) -> dict[str, Any]:
    facts = require_mapping(value, path)
    require_exact_fields(
        facts,
        {
            "checkpoint_id",
            "checkpoint_scope",
            "checkpoint_persisted",
            "source_reference",
        },
        path=path,
    )
    if not require_bool(
        facts["checkpoint_persisted"],
        f"{path}.checkpoint_persisted",
    ):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path}.checkpoint_persisted must be true",
        )
    return {
        "checkpoint_id": require_string(
            facts["checkpoint_id"],
            f"{path}.checkpoint_id",
        ),
        "checkpoint_scope": require_string(
            facts["checkpoint_scope"],
            f"{path}.checkpoint_scope",
        ),
        "checkpoint_persisted": True,
        "source_reference": _validate_source_reference(
            facts["source_reference"],
            f"{path}.source_reference",
        ),
    }


def _validate_heartbeat_facts(
    value: Any,
    path: str,
) -> dict[str, Any]:
    facts = require_mapping(value, path)
    require_exact_fields(
        facts,
        {
            "lease_id",
            "assigned_peer",
            "heartbeat_peer",
            "lease_open",
        },
        path=path,
    )
    if not require_bool(facts["lease_open"], f"{path}.lease_open"):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path}.lease_open must be true",
        )
    return {
        "lease_id": require_string(
            facts["lease_id"],
            f"{path}.lease_id",
        ),
        "assigned_peer": require_string(
            facts["assigned_peer"],
            f"{path}.assigned_peer",
        ),
        "heartbeat_peer": require_string(
            facts["heartbeat_peer"],
            f"{path}.heartbeat_peer",
        ),
        "lease_open": True,
    }


def _validate_retire_set_facts(
    value: Any,
    path: str,
) -> dict[str, Any]:
    facts = require_mapping(value, path)
    require_exact_fields(
        facts,
        {
            "retired_history_ids",
            "contender_active_ids",
            "winning_active_id",
        },
        path=path,
    )
    retired = _require_string_list(
        facts["retired_history_ids"],
        f"{path}.retired_history_ids",
    )
    contenders = _require_string_list(
        facts["contender_active_ids"],
        f"{path}.contender_active_ids",
    )
    winner = require_string(
        facts["winning_active_id"],
        f"{path}.winning_active_id",
    )
    if len(contenders) != 2 or winner not in contenders:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} requires two contenders and one named winner",
        )
    if set(retired) & set(contenders):
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{path} retired history cannot contain active contenders",
        )
    return {
        "retired_history_ids": retired,
        "contender_active_ids": contenders,
        "winning_active_id": winner,
    }


def _validate_fixture_vector(
    fixture_id: str,
    case_kind: str,
    facts: Mapping[str, Any],
) -> None:
    base = _base_fixture_id(fixture_id)
    expected_kind = {
        "CR-01": "SESSION_CLOSE",
        "CR-02": "DIRECT_READ",
        "CR-03": "BROADCAST",
        "CR-04": "CHECKPOINT",
        "CR-05": "OWNER_HEARTBEAT",
        "CR-06": "SERIALIZED_RETIRE_SET",
    }[base]
    if case_kind != expected_kind:
        raise DomainContractError(
            "DOMAIN_INPUT_INVALID",
            f"{base}.case_kind must be {expected_kind}",
        )

    if base == "CR-05":
        is_negative = fixture_id.endswith(_NEGATIVE_SUFFIX)
        is_owner = facts["heartbeat_peer"] == facts["assigned_peer"]
        if is_owner == is_negative:
            raise DomainContractError(
                "DOMAIN_INPUT_INVALID",
                (
                    f"{base} positive requires the current owner; "
                    "negative requires a non-owner heartbeat"
                ),
            )


def validate_coordination_room_inputs(
    fixture_id: str,
    raw_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one closed CR-01..06 coordination-room vector."""

    _base_fixture_id(fixture_id)
    inputs = require_mapping(raw_inputs, "inputs")
    require_exact_fields(
        inputs,
        {"case_kind", "facts"},
        path="inputs",
    )
    case_kind = _require_enum(
        inputs["case_kind"],
        _CASE_KINDS,
        "inputs.case_kind",
    )
    validators = {
        "SESSION_CLOSE": _validate_session_close_facts,
        "DIRECT_READ": _validate_direct_read_facts,
        "BROADCAST": _validate_broadcast_facts,
        "CHECKPOINT": _validate_checkpoint_facts,
        "OWNER_HEARTBEAT": _validate_heartbeat_facts,
        "SERIALIZED_RETIRE_SET": _validate_retire_set_facts,
    }
    facts = validators[case_kind](inputs["facts"], "inputs.facts")
    _validate_fixture_vector(fixture_id, case_kind, facts)
    return {"case_kind": case_kind, "facts": facts}


def _validate_details(
    decision: str,
    value: Any,
) -> dict[str, Any]:
    details = require_mapping(value, "output.details")

    if decision == "SESSION_CLOSED":
        require_exact_fields(
            details,
            {
                "session_id",
                "members",
                "state_transition_applied",
                "close_exit_code",
            },
            path="output.details",
        )
        return {
            "session_id": require_string(
                details["session_id"],
                "output.details.session_id",
            ),
            "members": _require_string_list(
                details["members"],
                "output.details.members",
            ),
            "state_transition_applied": require_bool(
                details["state_transition_applied"],
                "output.details.state_transition_applied",
            ),
            "close_exit_code": require_nonnegative_int(
                details["close_exit_code"],
                "output.details.close_exit_code",
            ),
        }

    if decision == "MESSAGE_READ":
        require_exact_fields(
            details,
            {
                "message_id",
                "thread_id",
                "sender",
                "recipient",
                "terminal_status",
                "read_transition_count",
            },
            path="output.details",
        )
        return {
            "message_id": require_nonnegative_int(
                details["message_id"],
                "output.details.message_id",
            ),
            "thread_id": require_string(
                details["thread_id"],
                "output.details.thread_id",
            ),
            "sender": require_string(
                details["sender"],
                "output.details.sender",
            ),
            "recipient": require_string(
                details["recipient"],
                "output.details.recipient",
            ),
            "terminal_status": _require_enum(
                details["terminal_status"],
                frozenset({"UNREAD", "READ"}),
                "output.details.terminal_status",
                output=True,
            ),
            "read_transition_count": require_nonnegative_int(
                details["read_transition_count"],
                "output.details.read_transition_count",
            ),
        }

    if decision == "BROADCAST_DELIVERED":
        require_exact_fields(
            details,
            {"thread_id", "recipient_order", "message_ids"},
            path="output.details",
        )
        return {
            "thread_id": require_string(
                details["thread_id"],
                "output.details.thread_id",
            ),
            "recipient_order": _require_string_list(
                details["recipient_order"],
                "output.details.recipient_order",
            ),
            "message_ids": _require_positive_int_list(
                details["message_ids"],
                "output.details.message_ids",
            ),
        }

    if decision == "CHECKPOINT_RECORDED":
        require_exact_fields(
            details,
            {
                "checkpoint_id",
                "checkpoint_scope",
                "checkpoint_persisted",
                "immutable_source_reference_recorded",
                "source_reference",
            },
            path="output.details",
        )
        return {
            "checkpoint_id": require_string(
                details["checkpoint_id"],
                "output.details.checkpoint_id",
            ),
            "checkpoint_scope": require_string(
                details["checkpoint_scope"],
                "output.details.checkpoint_scope",
            ),
            "checkpoint_persisted": require_bool(
                details["checkpoint_persisted"],
                "output.details.checkpoint_persisted",
            ),
            "immutable_source_reference_recorded": require_bool(
                details["immutable_source_reference_recorded"],
                (
                    "output.details."
                    "immutable_source_reference_recorded"
                ),
            ),
            "source_reference": _validate_source_reference(
                details["source_reference"],
                "output.details.source_reference",
                nullable=True,
                output=True,
            ),
        }

    if decision in {
        "HEARTBEAT_ACCEPTED",
        "HEARTBEAT_REJECTED",
    }:
        require_exact_fields(
            details,
            {
                "lease_id",
                "assigned_peer",
                "heartbeat_peer",
                "owner_heartbeat_accepted",
            },
            path="output.details",
        )
        return {
            "lease_id": require_string(
                details["lease_id"],
                "output.details.lease_id",
            ),
            "assigned_peer": require_string(
                details["assigned_peer"],
                "output.details.assigned_peer",
            ),
            "heartbeat_peer": require_string(
                details["heartbeat_peer"],
                "output.details.heartbeat_peer",
            ),
            "owner_heartbeat_accepted": require_bool(
                details["owner_heartbeat_accepted"],
                "output.details.owner_heartbeat_accepted",
            ),
        }

    require_exact_fields(
        details,
        {
            "serialized_transition",
            "retired_history_ids",
            "active_ids",
        },
        path="output.details",
    )
    return {
        "serialized_transition": require_bool(
            details["serialized_transition"],
            "output.details.serialized_transition",
        ),
        "retired_history_ids": _require_string_list(
            details["retired_history_ids"],
            "output.details.retired_history_ids",
        ),
        "active_ids": _require_string_list(
            details["active_ids"],
            "output.details.active_ids",
        ),
    }


def validate_coordination_room_output(
    fixture_id: str,
    raw_output: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate coordination-room observable output."""

    _base_fixture_id(fixture_id)
    output = require_mapping(raw_output, "output")
    require_exact_fields(
        output,
        {"rule_tier", "decision", "details"},
        path="output",
    )
    decision = _require_enum(
        output["decision"],
        _DECISIONS,
        "output.decision",
        output=True,
    )
    return {
        "rule_tier": _require_enum(
            output["rule_tier"],
            _RULE_TIERS,
            "output.rule_tier",
            output=True,
        ),
        "decision": decision,
        "details": _validate_details(decision, output["details"]),
    }


def _oracle_output(
    case_kind: str,
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    if case_kind == "SESSION_CLOSE":
        return {
            "rule_tier": "OBS",
            "decision": "SESSION_CLOSED",
            "details": {
                "session_id": facts["session_id"],
                "members": list(facts["members"]),
                "state_transition_applied": True,
                "close_exit_code": facts["close_exit_code"],
            },
        }
    if case_kind == "DIRECT_READ":
        return {
            "rule_tier": "OBS",
            "decision": "MESSAGE_READ",
            "details": {
                "message_id": facts["message_id"],
                "thread_id": facts["thread_id"],
                "sender": facts["sender"],
                "recipient": facts["recipient"],
                "terminal_status": "READ",
                "read_transition_count": 1,
            },
        }
    if case_kind == "BROADCAST":
        return {
            "rule_tier": "OBS",
            "decision": "BROADCAST_DELIVERED",
            "details": {
                "thread_id": facts["thread_id"],
                "recipient_order": list(facts["recipient_order"]),
                "message_ids": list(facts["message_ids"]),
            },
        }
    if case_kind == "CHECKPOINT":
        return {
            "rule_tier": "CANDIDATE",
            "decision": "CHECKPOINT_RECORDED",
            "details": {
                "checkpoint_id": facts["checkpoint_id"],
                "checkpoint_scope": facts["checkpoint_scope"],
                "checkpoint_persisted": True,
                "immutable_source_reference_recorded": True,
                "source_reference": dict(facts["source_reference"]),
            },
        }
    if case_kind == "OWNER_HEARTBEAT":
        accepted = facts["heartbeat_peer"] == facts["assigned_peer"]
        return {
            "rule_tier": "OBS",
            "decision": (
                "HEARTBEAT_ACCEPTED"
                if accepted
                else "HEARTBEAT_REJECTED"
            ),
            "details": {
                "lease_id": facts["lease_id"],
                "assigned_peer": facts["assigned_peer"],
                "heartbeat_peer": facts["heartbeat_peer"],
                "owner_heartbeat_accepted": accepted,
            },
        }
    return {
        "rule_tier": "OBS",
        "decision": "SERIALIZED_TRANSITION",
        "details": {
            "serialized_transition": True,
            "retired_history_ids": list(
                facts["retired_history_ids"]
            ),
            "active_ids": [facts["winning_active_id"]],
        },
    }


def _reference_output(
    case_kind: str,
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    if case_kind == "SESSION_CLOSE":
        details = {
            "session_id": facts["session_id"],
            "members": [member for member in facts["members"]],
            "state_transition_applied": (
                facts["state_before_digest"]
                != facts["state_after_digest"]
            ),
            "close_exit_code": facts["close_exit_code"],
        }
        return {
            "rule_tier": "OBS",
            "decision": "SESSION_CLOSED",
            "details": details,
        }
    if case_kind == "DIRECT_READ":
        return {
            "rule_tier": "OBS",
            "decision": "MESSAGE_READ",
            "details": {
                "message_id": facts["message_id"],
                "thread_id": facts["thread_id"],
                "sender": facts["sender"],
                "recipient": facts["recipient"],
                "terminal_status": (
                    "READ"
                    if facts["mark_read_requests"] == 1
                    else "UNREAD"
                ),
                "read_transition_count": facts["mark_read_requests"],
            },
        }
    if case_kind == "BROADCAST":
        pairs = list(
            zip(
                facts["recipient_order"],
                facts["message_ids"],
                strict=True,
            )
        )
        return {
            "rule_tier": "OBS",
            "decision": "BROADCAST_DELIVERED",
            "details": {
                "thread_id": facts["thread_id"],
                "recipient_order": [pair[0] for pair in pairs],
                "message_ids": [pair[1] for pair in pairs],
            },
        }
    if case_kind == "CHECKPOINT":
        reference = facts["source_reference"]
        return {
            "rule_tier": "CANDIDATE",
            "decision": "CHECKPOINT_RECORDED",
            "details": {
                "checkpoint_id": facts["checkpoint_id"],
                "checkpoint_scope": facts["checkpoint_scope"],
                "checkpoint_persisted": facts["checkpoint_persisted"],
                "immutable_source_reference_recorded": (
                    reference is not None
                ),
                "source_reference": dict(reference),
            },
        }
    if case_kind == "OWNER_HEARTBEAT":
        owner_match = (
            facts["assigned_peer"] == facts["heartbeat_peer"]
        )
        return {
            "rule_tier": "OBS",
            "decision": (
                "HEARTBEAT_ACCEPTED"
                if owner_match
                else "HEARTBEAT_REJECTED"
            ),
            "details": {
                "lease_id": facts["lease_id"],
                "assigned_peer": facts["assigned_peer"],
                "heartbeat_peer": facts["heartbeat_peer"],
                "owner_heartbeat_accepted": owner_match,
            },
        }
    retained_history = [
        item for item in facts["retired_history_ids"]
    ]
    return {
        "rule_tier": "OBS",
        "decision": "SERIALIZED_TRANSITION",
        "details": {
            "serialized_transition": True,
            "retired_history_ids": retained_history,
            "active_ids": [facts["winning_active_id"]],
        },
    }


class AuthorityCoordinationRoomOracle:
    """Pure expected coordination-room transition oracle."""

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
        return _oracle_output(
            raw_inputs["case_kind"],
            raw_inputs["facts"],
        )


class CoordinationRoomSubjectAdapter:
    """Pure reference adapter over injected coordination-room facts."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = f"coordination_room.{label}.reference"
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
                f"adapter={self.adapter_id};fixture_id={fixture_id}",
            )
        return _reference_output(
            raw_inputs["case_kind"],
            raw_inputs["facts"],
        )


class FaultInjectedCoordinationRoomAdapter:
    """One fixture-specific coordination-room defect."""

    adapter_version = 1

    def __init__(self, base_fixture_id: str) -> None:
        self._base = base_fixture_id
        self._fixture_id = f"{base_fixture_id}{_NEGATIVE_SUFFIX}"
        label = base_fixture_id.lower().replace("-", "")
        self.adapter_id = f"coordination_room.{label}.fault"
        self.fixture_ids = frozenset({self._fixture_id})

    def execute(
        self,
        fixture_id: str,
        raw_inputs: Mapping[str, Any],
        context: IsolatedDomainContext,
    ) -> Mapping[str, Any]:
        if fixture_id != self._fixture_id:
            raise DomainContractError(
                "DOMAIN_FIXTURE_UNSUPPORTED",
                f"adapter={self.adapter_id};fixture_id={fixture_id}",
            )

        output = _reference_output(
            raw_inputs["case_kind"],
            raw_inputs["facts"],
        )
        details = output["details"]

        if self._base == "CR-01":
            details["state_transition_applied"] = False
        elif self._base == "CR-02":
            details["read_transition_count"] = 2
        elif self._base == "CR-03":
            details["message_ids"] = list(
                reversed(details["message_ids"])
            )
        elif self._base == "CR-04":
            details["source_reference"] = None
        elif self._base == "CR-05":
            details["owner_heartbeat_accepted"] = True
            output["decision"] = "HEARTBEAT_ACCEPTED"
        else:
            details["active_ids"] = list(
                raw_inputs["facts"]["contender_active_ids"]
            )

        return output


def coordination_room_registrations(
) -> tuple[DomainRegistration, ...]:
    """Return all CR-01..06 coordination-room registrations."""

    registrations: list[DomainRegistration] = []
    for base_fixture_id in _BASE_FIXTURES:
        oracle = AuthorityCoordinationRoomOracle(base_fixture_id)
        registrations.append(
            DomainRegistration(
                fixture_id=base_fixture_id,
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=CoordinationRoomSubjectAdapter(
                    base_fixture_id
                ),
                input_validator=validate_coordination_room_inputs,
                output_validator=validate_coordination_room_output,
            )
        )
        registrations.append(
            DomainRegistration(
                fixture_id=f"{base_fixture_id}{_NEGATIVE_SUFFIX}",
                oracle_id=oracle.oracle_id,
                oracle_version=oracle.oracle_version,
                oracle=oracle,
                adapter=FaultInjectedCoordinationRoomAdapter(
                    base_fixture_id
                ),
                input_validator=validate_coordination_room_inputs,
                output_validator=validate_coordination_room_output,
            )
        )
    return tuple(registrations)
