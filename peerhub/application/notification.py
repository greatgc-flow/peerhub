"""NOTIFY consultation target (R4 8.2 CD-NF-01..03).

NOTIFY is non-blocking but the design requires a durable *notification target*: it records the
dispatch reference (a digest, never the raw query), the authorized recipients and the delivery
state. Delivery itself is not implemented yet, so the honest state is ``undelivered`` (enqueue is
never reported as a read receipt); dispatch proceeds regardless (CD-NF-03).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import cast

from peerhub.core.context import Clock, IdSource
from peerhub.core.protocol import JsonValue
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.contract import (
    EffectIntent,
    MutationSubmission,
    build_mutation_request,
)

DELIVERY_UNDELIVERED = "undelivered"


def create_notification_target(
    broker: GovernanceBroker,
    *,
    clock: Clock,
    ids: IdSource,
    action: str,
    prompt: str,
    recipients: Sequence[str],
    actor_id: str,
) -> MutationSubmission:
    """Record one dispatch notification target; never blocks and never stores the raw prompt."""

    notification_id = f"dispatch-notification:{ids.new_id('notification')}"
    state = cast(dict[str, JsonValue], {
        "schema": "peerhub.dispatch-notification.v1",
        "kind": "dispatch-notification",
        "notification_id": notification_id,
        "action": action,
        "prompt_digest": "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "recipients": list(recipients),
        "delivery_state": DELIVERY_UNDELIVERED,
        "created_at": clock.now(),
        "created_by": actor_id,
    })
    return broker.submit(
        build_mutation_request(
            ids,
            id_prefix="notification",
            client_id="peerhub.dispatch-notification",
            target_id=notification_id,
            expected_revision=0,
            actor_id=actor_id,
            operation="dispatch-notification.create",
            desired_state=state,
            effect_intent=EffectIntent(kind="dispatch-notification.noop", payload={}),
        )
    )
