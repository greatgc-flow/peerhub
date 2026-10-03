from pathlib import Path
import time
import pytest
from datetime import datetime, timezone, timedelta

from peerhub.m1.models import Peer, Stream, StreamState, Record
from peerhub.m1.store import CoreStore
from peerhub.extensions.observation_and_diag import (
    Observation,
    ObservationStore,
    ObservationKind,
    EvidenceState,
    ReadonlyDiag,
)
from peerhub.extensions.session_bridge import SessionBridgeStore, ExecutionCertainty


def test_observation_freshness_and_honesty(tmp_path: Path):
    obs_store = ObservationStore(tmp_path / "obs.db")

    # Record a quota observation
    obs = Observation(
        observation_id="obs-quota-1",
        subject_ref="provider:anthropic",
        kind=ObservationKind.QUOTA,
        source="cli-adapter",
        state=EvidenceState.MEASURED,
        payload={"tier": "usage-tier-4", "remaining_tokens": 50000},
        ttl_seconds=2,
    )
    obs_store.record_observation(obs)

    # Immediately fetch: should be MEASURED
    latest = obs_store.get_latest_observation("provider:anthropic", ObservationKind.QUOTA)
    assert latest is not None
    assert latest.state == EvidenceState.MEASURED
    assert latest.payload["remaining_tokens"] == 50000

    # Simulate passage of time past TTL -> evaluates to STALE on read
    future = datetime.now(timezone.utc) + timedelta(seconds=10)
    stale_eval = latest.evaluated_state(as_of=future)
    assert stale_eval == EvidenceState.STALE


def test_readonly_diag_observes_without_mutation(tmp_path: Path):
    core_db = tmp_path / "core.db"
    obs_db = tmp_path / "obs.db"

    core = CoreStore(core_db)
    core.register_peer(Peer(peer_id="p1"))
    core.create_stream(Stream(stream_id="s1", members=["p1"]))
    core.append_record(
        created_at="2026-10-01T00:00:00Z",
        stream_id="s1",
        author_peer_id="p1",
        kind="test",
        body={"hello": "world"},
        idempotency_key="k1",
    )
    core.advance_offset_cas("p1", "s1", new_position=1, expected_revision=1)

    diag = ReadonlyDiag(core_db, obs_db)
    health = diag.inspect_stream_health("s1")

    assert health["status"] == "OK"
    assert health["stream_id"] == "s1"
    assert health["head_position"] == 1
    assert health["offsets"]["p1"] == 1


def test_session_bridge_single_active_claim(tmp_path: Path):
    bridge_store = SessionBridgeStore(tmp_path / "bridge.db")

    # Bridge A acquires claim
    claimed = bridge_store.acquire_delivery_claim(
        workspace_id="ws-1",
        stream_id="stream-1",
        peer_id="peer-codex-1",
        bridge_id="bridge-A",
        lease_duration_sec=2.0,
    )
    assert claimed is True

    # Bridge B attempts concurrent acquire -> must fail
    claimed_b = bridge_store.acquire_delivery_claim(
        workspace_id="ws-1",
        stream_id="stream-1",
        peer_id="peer-codex-1",
        bridge_id="bridge-B",
        lease_duration_sec=2.0,
    )
    assert claimed_b is False

    # After lease expires, Bridge B can acquire
    time.sleep(2.1)
    claimed_b_after = bridge_store.acquire_delivery_claim(
        workspace_id="ws-1",
        stream_id="stream-1",
        peer_id="peer-codex-1",
        bridge_id="bridge-B",
        lease_duration_sec=2.0,
    )
    assert claimed_b_after is True
