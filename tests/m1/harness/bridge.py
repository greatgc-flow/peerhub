"""Bridge harness adapter (TEST_HARNESS_PORT.md). Thin translation only; semantics live in peerhub.extensions.bridge."""
from __future__ import annotations

from pathlib import Path

from peerhub.extensions.bridge import Bridge
from peerhub.extensions.bridge_claims import ClaimStore
from peerhub.m1.store import CoreStore
from tests.m1.harness.clock import ManualClock
from tests.m1.harness.core import CoreHarness


class BridgeHarness(CoreHarness):
    def __init__(self, workspace: Path, clock: ManualClock | None = None, *, fault_hook=None, store_hook=None, **bridge_kw) -> None:
        super().__init__(workspace)
        self.clock = clock or ManualClock()
        self._fault_hook, self._store_hook, self._bridge_kw = fault_hook, store_hook, bridge_kw
        self._build()

    def _build(self) -> None:
        self.store = CoreStore(self.db_path, fault_hook=self._store_hook)
        self.cs = ClaimStore(self.db_path, generation=self.ws.generation, clock=self.clock.now)
        self.bridge = Bridge(self.store, self.cs, fault_hook=self._fault_hook, **self._bridge_kw)

    def reopen(self) -> "BridgeHarness":
        self._build()
        return self

    def new_bridge(self, owner_id: str, **kw) -> Bridge:
        """Independent bridge instance (own connections) sharing the same workspace, for races between owners."""
        cs = ClaimStore(self.db_path, generation=self.ws.generation, clock=self.clock.now)
        return Bridge(CoreStore(self.db_path), cs, owner_id=owner_id, **{**self._bridge_kw, **kw})

    def acquire_claim(self, peer_id, stream_id, owner_id, lease_sec=30.0):
        return self.cs.acquire(peer_id, stream_id, owner_id, lease_sec)

    def renew_claim(self, token):
        return self.cs.renew(token)

    def heartbeat(self, token):
        return self.cs.heartbeat(token)

    def delivery_cycle(self, peer_id, stream_id, runtime_target):
        return self.bridge.delivery_cycle(peer_id, stream_id, runtime_target)

    def current_mapping(self, peer_id, stream_id):
        return self.bridge.current_mapping(peer_id, stream_id)

    def execution_evidence(self, record_id, peer_id):
        return self.bridge.execution_evidence(record_id, peer_id)

    def finalize_terminal(self, claim_token, result, delivery_id=None):
        return self.bridge.finalize_terminal(claim_token, result, delivery_id or self.bridge.latest_delivery_id(claim_token))

    def handle_control(self, record_id, runtime_target):  # Wave 4 (CTL)
        raise NotImplementedError("handle_control arrives with Wave 4")

    def reconcile_uncertain(self, record_id, reconciliation_record):
        return self.bridge.reconcile_uncertain(record_id, reconciliation_record)
