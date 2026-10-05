"""M2.2 Work Projection Test Suite (WRK-001..012).

Verifies:
- WRK-001: Stream records append successfully before projection updates (record authority).
- WRK-002: Replaying ordered stream records reconstructs identical work projection state.
- WRK-003: Work lifecycle transitions OPEN -> ACTIVE <-> BLOCKED/PAUSED -> DONE/FAILED.
- WRK-004: Work cancellation from non-terminal states succeeds.
- WRK-005: Concurrent transition on same revision rejects second caller with WorkRevisionConflictError.
- WRK-006: Forbidden transition from terminal states raises ForbiddenTransitionError.
- WRK-007: Querying or mutating non-existent work_id raises WorkNotFoundError.
- WRK-008: Malformed record payload in stream is gracefully ignored by reducer.
- WRK-009: Duplicate work creation with identical ID raises WorkAlreadyExistsError.
- WRK-010: Checkpoints save intermediate execution state and increment revision.
- WRK-011: Linking artifact digest to work item attaches digest reference immutably.
- WRK-012: Wiping projection SQLite table and calling rebuild_projection restores full state.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
import pytest

from peerhub.m1.models import Peer, Stream, StreamState
from peerhub.m1.store import CoreStore
from peerhub.m2.work import (
    WorkProjection,
    WorkItem,
    WorkNotFoundError,
    WorkRevisionConflictError,
    WorkAlreadyExistsError,
    ForbiddenTransitionError,
)


@pytest.fixture
def store(tmp_path: Path) -> CoreStore:
    s = CoreStore(tmp_path / "core.db")
    s.register_peer(Peer(peer_id="p1", display_name="Peer 1"))
    s.create_stream(Stream(stream_id="s1", members=["p1"], state=StreamState.OPEN))
    return s


@pytest.fixture
def projection(tmp_path: Path, store: CoreStore) -> WorkProjection:
    db_path = tmp_path / "work_projection.db"
    return WorkProjection(db_path, store=store)


def test_wrk_001_stream_records_authoritative_for_projection(projection: WorkProjection, store: CoreStore):
    """WRK-001: Stream record is durable; projection reduces it to OPEN state with revision 1."""
    work = projection.create_work(
        stream_id="s1",
        work_id="w-101",
        title="Initial Task",
        spec={"task_type": "analysis"},
    )

    assert work.work_id == "w-101"
    assert work.stream_id == "s1"
    assert work.state == "OPEN"
    assert work.revision == 1
    assert work.title == "Initial Task"

    # Verify that an authoritative record exists in the core stream
    records = store.read_records(stream_id="s1")
    work_records = [r for r in records if r.kind == "m2.work.created"]
    assert len(work_records) == 1
    assert isinstance(work_records[0].body, dict)
    assert work_records[0].body["work_id"] == "w-101"


def test_wrk_003_lifecycle_transitions(projection: WorkProjection):
    """WRK-003: Work lifecycle transitions OPEN -> ACTIVE <-> BLOCKED/PAUSED -> DONE/FAILED."""
    projection.create_work("s1", "w-lifecycle", "Lifecycle Task", {})

    # OPEN -> ACTIVE
    w1 = projection.transition_work("w-lifecycle", expected_revision=1, target_state="ACTIVE")
    assert w1.state == "ACTIVE"
    assert w1.revision == 2

    # ACTIVE -> PAUSED
    w2 = projection.transition_work("w-lifecycle", expected_revision=2, target_state="PAUSED")
    assert w2.state == "PAUSED"
    assert w2.revision == 3

    # PAUSED -> ACTIVE
    w3 = projection.transition_work("w-lifecycle", expected_revision=3, target_state="ACTIVE")
    assert w3.state == "ACTIVE"
    assert w3.revision == 4

    # ACTIVE -> BLOCKED
    w4 = projection.transition_work("w-lifecycle", expected_revision=4, target_state="BLOCKED")
    assert w4.state == "BLOCKED"
    assert w4.revision == 5

    # BLOCKED -> ACTIVE
    w5 = projection.transition_work("w-lifecycle", expected_revision=5, target_state="ACTIVE")
    assert w5.state == "ACTIVE"
    assert w5.revision == 6

    # ACTIVE -> DONE
    w6 = projection.transition_work("w-lifecycle", expected_revision=6, target_state="DONE")
    assert w6.state == "DONE"
    assert w6.revision == 7


def test_wrk_004_cancellation_from_non_terminal_states(projection: WorkProjection):
    """WRK-004: Cancellation from non-terminal states succeeds."""
    projection.create_work("s1", "w-cancel-open", "Cancel Open", {})
    w_open = projection.transition_work("w-cancel-open", expected_revision=1, target_state="CANCELLED")
    assert w_open.state == "CANCELLED"
    assert w_open.revision == 2

    projection.create_work("s1", "w-cancel-active", "Cancel Active", {})
    projection.transition_work("w-cancel-active", expected_revision=1, target_state="ACTIVE")
    w_act = projection.transition_work("w-cancel-active", expected_revision=2, target_state="CANCELLED")
    assert w_act.state == "CANCELLED"
    assert w_act.revision == 3


def test_wrk_005_concurrent_transition_revision_conflict(projection: WorkProjection):
    """WRK-005: Concurrent transition on same revision rejects second caller with WorkRevisionConflictError."""
    projection.create_work("s1", "w-race", "Race Task", {})

    # Caller 1 transitions expected_revision=1 -> ACTIVE (success)
    projection.transition_work("w-race", expected_revision=1, target_state="ACTIVE")

    # Caller 2 also tries to transition expected_revision=1 (conflict!)
    with pytest.raises(WorkRevisionConflictError):
        projection.transition_work("w-race", expected_revision=1, target_state="CANCELLED")


def test_wrk_006_forbidden_transition_from_terminal_states(projection: WorkProjection):
    """WRK-006: Forbidden transition from terminal states raises ForbiddenTransitionError."""
    projection.create_work("s1", "w-term", "Terminal Task", {})
    projection.transition_work("w-term", expected_revision=1, target_state="ACTIVE")
    projection.transition_work("w-term", expected_revision=2, target_state="DONE")

    # Cannot transition out of DONE
    with pytest.raises(ForbiddenTransitionError):
        projection.transition_work("w-term", expected_revision=3, target_state="ACTIVE")

    # Cannot transition directly from OPEN to DONE
    projection.create_work("s1", "w-jump", "Jump Task", {})
    with pytest.raises(ForbiddenTransitionError):
        projection.transition_work("w-jump", expected_revision=1, target_state="DONE")


def test_wrk_007_work_not_found(projection: WorkProjection):
    """WRK-007: Querying or mutating non-existent work_id raises WorkNotFoundError."""
    with pytest.raises(WorkNotFoundError):
        projection.get_work("w-non-existent")

    with pytest.raises(WorkNotFoundError):
        projection.transition_work("w-non-existent", expected_revision=1, target_state="ACTIVE")


def test_wrk_008_malformed_record_ignored_by_reducer(projection: WorkProjection, store: CoreStore):
    """WRK-008: Malformed record payload in stream is gracefully ignored without crashing projection."""
    projection.create_work("s1", "w-valid", "Valid Task", {})

    # Append malformed record directly to stream
    store.append_record(
        stream_id="s1",
        author_peer_id="p1",
        kind="m2.work.transitioned",
        body={"corrupted": "no_work_id"},
        idempotency_key="bad-rec-1",
        created_at="2026-10-05T12:00:00Z",
    )

    # Next legitimate transition still functions
    w = projection.transition_work("w-valid", expected_revision=1, target_state="ACTIVE")
    assert w.state == "ACTIVE"


def test_wrk_009_duplicate_work_creation_rejected(projection: WorkProjection):
    """WRK-009: Attempting to create an existing work_id raises WorkAlreadyExistsError."""
    projection.create_work("s1", "w-dup", "Duplicate Task", {})

    with pytest.raises(WorkAlreadyExistsError):
        projection.create_work("s1", "w-dup", "Duplicate Task 2", {})


def test_wrk_010_checkpoints_save_state_and_increment_revision(projection: WorkProjection):
    """WRK-010: Checkpoints save intermediate progress and increment revision."""
    projection.create_work("s1", "w-chk", "Checkpoint Task", {})
    projection.transition_work("w-chk", expected_revision=1, target_state="ACTIVE")

    w1 = projection.checkpoint_work(
        "w-chk",
        expected_revision=2,
        checkpoint_data={"step": 5, "progress": 0.5},
    )

    assert w1.revision == 3
    assert w1.checkpoint == {"step": 5, "progress": 0.5}


def test_wrk_011_link_artifact_to_work(projection: WorkProjection):
    """WRK-011: Linking artifact digest attaches reference immutably."""
    projection.create_work("s1", "w-art", "Artifact Task", {})
    projection.transition_work("w-art", expected_revision=1, target_state="ACTIVE")

    digest = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    w = projection.link_artifact("w-art", expected_revision=2, digest=digest)

    assert w.revision == 3
    assert digest in w.artifacts


def test_wrk_002_replaying_records_reconstructs_identical_projection_state(
    projection: WorkProjection, store: CoreStore, tmp_path: Path
):
    """WRK-002: Replaying ordered stream records reconstructs identical work projection state."""
    projection.create_work("s1", "w-1", "Task 1", {"priority": "high"})
    projection.transition_work("w-1", expected_revision=1, target_state="ACTIVE")
    projection.checkpoint_work("w-1", expected_revision=2, checkpoint_data={"cursor": 100})
    digest = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    projection.link_artifact("w-1", expected_revision=3, digest=digest)
    projection.transition_work("w-1", expected_revision=4, target_state="DONE")

    projection.create_work("s1", "w-2", "Task 2", {})
    projection.transition_work("w-2", expected_revision=1, target_state="CANCELLED")

    before_w1 = projection.get_work("w-1")
    before_w2 = projection.get_work("w-2")

    # Brand new projection DB instance pointing to clean path
    fresh_db = tmp_path / "rebuilt_projection_wrk002.db"
    fresh_proj = WorkProjection(fresh_db, store=store)

    all_records = store.read_records(stream_id="s1")
    reduced_count = fresh_proj.rebuild_projection(all_records)

    assert reduced_count >= 6
    after_w1 = fresh_proj.get_work("w-1")
    after_w2 = fresh_proj.get_work("w-2")

    assert after_w1.work_id == before_w1.work_id
    assert after_w1.state == before_w1.state == "DONE"
    assert after_w1.revision == before_w1.revision == 5
    assert after_w1.checkpoint == before_w1.checkpoint
    assert after_w1.artifacts == before_w1.artifacts

    assert after_w2.work_id == before_w2.work_id
    assert after_w2.state == before_w2.state == "CANCELLED"
    assert after_w2.revision == before_w2.revision == 2


def test_wrk_012_wipe_sqlite_table_rebuild_restores_state(
    projection: WorkProjection, store: CoreStore
):
    """WRK-012: Wiping projection SQLite table and calling rebuild_projection restores full state."""
    projection.create_work("s1", "w-table-wipe", "Table Wipe Task", {"mode": "test"})
    projection.transition_work("w-table-wipe", expected_revision=1, target_state="ACTIVE")
    projection.checkpoint_work("w-table-wipe", expected_revision=2, checkpoint_data={"progress": 80})
    before = projection.get_work("w-table-wipe")

    # Intentionally DROP the table from SQLite to simulate catastrophic table drop
    with sqlite3.connect(projection.db_path) as conn:
        conn.execute("DROP TABLE ext_work_items;")

    # Verify query now fails
    with pytest.raises(sqlite3.OperationalError):
        with sqlite3.connect(projection.db_path) as conn:
            conn.execute("SELECT * FROM ext_work_items;")

    # Rebuild projection directly on the existing projection instance
    all_records = store.read_records(stream_id="s1")
    reduced_count = projection.rebuild_projection(all_records)
    assert reduced_count >= 3

    # Verify restored state
    after = projection.get_work("w-table-wipe")
    assert after.work_id == before.work_id
    assert after.state == before.state == "ACTIVE"
    assert after.revision == before.revision == 3
    assert after.checkpoint == before.checkpoint == {"progress": 80}

