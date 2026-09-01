"""Read-only presentation of unfinished governance effect deliveries."""

from __future__ import annotations

from collections.abc import Mapping

from peerhub.core.protocol import JsonValue
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.contract import OutboxState, RecoveryDisposition


MAX_VISIBLE_EFFECT_DELIVERIES = 20


def collect_effect_status(
    broker: GovernanceBroker,
    *,
    limit: int = MAX_VISIBLE_EFFECT_DELIVERIES,
) -> Mapping[str, JsonValue]:
    """Return a bounded page of committed, unfinished effect deliveries."""

    if type(limit) is not int or not 1 <= limit <= MAX_VISIBLE_EFFECT_DELIVERIES:
        raise ValueError(
            "limit must be an integer between 1 and "
            f"{MAX_VISIBLE_EFFECT_DELIVERIES}"
        )

    recovered = broker.recover_pending_effects(limit=21)
    visible = recovered[:limit]
    deliveries: list[JsonValue] = []
    state_counts = {
        OutboxState.PENDING.value: 0,
        OutboxState.CLAIMED.value: 0,
    }
    disposition_counts = {
        RecoveryDisposition.READY_TO_CLAIM.value: 0,
        RecoveryDisposition.CONFIRMATION_REQUIRED.value: 0,
    }

    for pending in visible:
        event = pending.event
        effect_kind = event.payload.get("effect_kind")
        if not isinstance(effect_kind, str):
            raise RuntimeError(
                "governance effect event has no effect kind"
            )
        state_counts[event.state.value] += 1
        disposition_counts[pending.disposition.value] += 1
        deliveries.append({
            "event_id": event.event_id,
            "outbox_state": event.state.value,
            "recovery_disposition": pending.disposition.value,
            "effect_kind": effect_kind,
            "target_id": pending.transition_receipt.target_id,
            "target_revision": pending.transition_receipt.next_revision,
        })

    return {
        "deliveries": tuple(deliveries),
        "has_more": len(recovered) > limit,
        "visible_unfinished_count": len(deliveries),
        "visible_unfinished_count_by_state": state_counts,
        "visible_unfinished_count_by_disposition": disposition_counts,
    }


__all__ = [
    "MAX_VISIBLE_EFFECT_DELIVERIES",
    "collect_effect_status",
]
