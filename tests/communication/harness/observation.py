"""Observation + Diag harness port (TEST_HARNESS_PORT.md). Thin adapters only."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from peerhub.extensions.diag import ReadonlyDiag
from peerhub.extensions.observation import ObservationStore
from peerhub.extensions.observation_model import ResourcePool, parse_observation_wire, parse_resource_pool_wire
from tests.communication.fakes.observation import SequentialIdSource
from tests.communication.harness.clock import ManualClock
from tests.communication.harness.core import CoreHarness

CORE_TABLES = ("peers", "streams", "stream_members", "records", "offsets")
OBS_COLS = "capture_seq, observation_id, subject_ref, resource_pool_ref, kind, source, state, payload_json, observed_at, captured_at"
T0 = 1790812800.0  # 2026-10-01T00:00:00Z


class ObservationHarness(CoreHarness):
    def __init__(self, workspace: Path, clock: ManualClock | None = None, *, policy=None, fault_hook=None) -> None:
        super().__init__(workspace)
        self.clock = clock or ManualClock(start=T0)
        self._policy, self._obs_hook = policy, fault_hook
        self.ids = SequentialIdSource("obs")
        self._build_obs()

    def _build_obs(self) -> None:
        self.obs = ObservationStore(self.db_path, clock=self.clock.now, policy=self._policy, id_source=self.ids, fault_hook=self._obs_hook)

    def reopen(self) -> "ObservationHarness":
        super().reopen()
        self._build_obs()
        return self

    # --- Observation port
    def register_pool(self, spec: dict) -> ResourcePool:
        return self.obs.register_resource_pool(ResourcePool.model_validate(spec))

    def capture(self, subject_ref, kind, source_adapter, resource_pool_ref=None):
        return self.obs.capture(subject_ref, kind, source_adapter, resource_pool_ref=resource_pool_ref)

    def latest(self, subject_ref, kind, read_at=None, resource_pool_ref=None):
        return self.obs.latest(subject_ref, kind, read_at, resource_pool_ref=resource_pool_ref)

    def list_for_resource_pool(self, resource_pool_ref, read_at=None, **kw):
        return self.obs.list_for_resource_pool(resource_pool_ref, read_at, **kw)

    # --- Diag port
    def render_diag(self, workspace=None, sections=None, read_at=None, **diag_kw):
        db = self.db_path if workspace is None else Path(workspace) / Path(self.db_path).name
        return ReadonlyDiag(db, **diag_kw).render(sections, self.clock.now() if read_at is None else read_at)

    # --- wire boundary incl. extension-owned models (D-W0-2)
    def persist_wire(self, kind, obj):
        if kind == "peer-observation":
            return self.obs.persist(parse_observation_wire(obj))
        if kind == "resource-pool":
            return self.obs.register_resource_pool(parse_resource_pool_wire(obj))
        return super().persist_wire(kind, obj)

    # --- raw-SQL oracles (independent of the Observation read path)
    def obs_rows(self) -> list[tuple]:
        with closing(sqlite3.connect(self.db_path)) as c:
            return c.execute(f"SELECT {OBS_COLS} FROM observations ORDER BY capture_seq").fetchall()

    def pool_rows(self) -> list[tuple]:
        with closing(sqlite3.connect(self.db_path)) as c:
            return c.execute("SELECT resource_pool_id, provider, kind, metadata_json FROM resource_pools ORDER BY 1").fetchall()

    def core_digests(self) -> dict[str, str]:
        return {t: d for t, d in self.table_digests().items() if t in CORE_TABLES}

    def full_state(self) -> tuple:
        """Everything that must not change on a rejected/isolated operation."""
        return (self.state_digest(), self.row_counts(), self.table_digests())
