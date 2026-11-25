"""Session Bridge façade over durable control, sessions, and fenced delivery."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from peerhub.core.store import CoreStore
from peerhub.extensions import bridge_schema
from peerhub.extensions.bridge_claims import ClaimStore
from peerhub.extensions.bridge_delivery import BridgeDelivery
from peerhub.extensions.bridge_sessions import DEFAULT_CATCH_UP_BYTES
from peerhub.extensions.catchup import CatchUpBudget

class Bridge(BridgeDelivery):
    def __init__(self, store: CoreStore, claims: ClaimStore, *, owner_id: str = "bridge-1", lease_sec: float = 30.0,
                 max_session_attempts: int = 3, catch_up_limit: int = 100, ack_retries: int = 8,
                 catch_up_budget: CatchUpBudget | None = None, fault_hook: Callable[[str], None] | None = None,
                 caller: Mapping[str, Any] | None = None,
                 stream_budget: tuple[int, int] | None = None, on_stream_budget: Callable[[], None] | None = None) -> None:
        self.store, self.claims = store, claims
        # #374: caller detail (schema v1, peerhub.extensions.caller); evidence only, never part of any digest.
        self.caller: dict[str, Any] | None = dict(caller) if isinstance(caller, dict) else None
        self.owner_id, self.lease_sec = owner_id, lease_sec
        self.max_session_attempts, self.catch_up_limit, self.ack_retries = max_session_attempts, catch_up_limit, ack_retries
        # Leave room for the current ask and prompt headers within AG's 30,000-byte Windows inline limit.
        self.catch_up_budget = catch_up_budget if catch_up_budget is not None else CatchUpBudget(max_records=catch_up_limit, max_bytes=DEFAULT_CATCH_UP_BYTES)
        self._hook = fault_hook
        self._stream_budget, self._on_stream_budget = stream_budget, on_stream_budget
        bridge_schema.initialize(claims, self._fire)

    def _fire(self, point: str) -> None:
        if self._hook is not None:
            self._hook(point)
