from pathlib import Path
import time
import pytest
from datetime import datetime, timezone, timedelta

from peerhub.m1.models import Peer, Stream, StreamState, Record
from peerhub.m1.store import CoreStore
from peerhub.extensions.session_bridge import SessionBridgeStore, ExecutionCertainty


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
