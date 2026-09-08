import pytest
from pathlib import Path
from peerhub.governance.file_locks import (
    FileUnlockDisposition,
)
from peerhub.core.errors import (
    FileLockConflictError,
    FileLockOwnershipMismatchError,
)
from peerhub.application.legacy import (
    LockAcquireCommand,
    LockReleaseCommand,
    LockStatusCommand,
    SubmissionMetadata,
)
from peerhub.core.context import RuntimeContext, PathLayout
from peerhub.runtime import create_runtime
from tests.integration.conftest import FakeClock, FakeIdSource

def test_basic_lock_unlock(tmp_path: Path) -> None:
    layout = PathLayout.for_workspace(tmp_path)
    context = RuntimeContext("home-1", layout, FakeClock(), FakeIdSource())
    with create_runtime(context) as runtime:
        service = runtime.file_lock_service
        
        # Lock
        submission = service.lock_file(name="foo.txt", owner="alice", lock_scope="file")
        assert submission.receipt.status.value == "COMMITTED_ENFORCEMENT_PENDING"
        
        # List
        locks = service.list_active_locks()
        assert len(locks) == 1
        assert locks[0].state["name"] == "foo.txt"
        assert locks[0].state["owner"] == "alice"
        assert locks[0].state["lock_scope"] == "file"
        
        # Unlock
        result = service.unlock_file(name="foo.txt", owner="alice")
        assert result.disposition == FileUnlockDisposition.RELEASED
        
        # List again
        assert len(service.list_active_locks()) == 0

def test_idempotent_re_lock(tmp_path: Path) -> None:
    layout = PathLayout.for_workspace(tmp_path)
    context = RuntimeContext("home-1", layout, FakeClock(), FakeIdSource())
    with create_runtime(context) as runtime:
        service = runtime.file_lock_service
        
        # First lock
        service.lock_file(name="foo.txt", owner="alice", lock_scope="file")
        locks = service.list_active_locks()
        first_locked_at = locks[0].state["locked_at"]
        
        # Re-lock same owner, new scope
        service.lock_file(name="foo.txt", owner="alice", lock_scope="project")
        locks = service.list_active_locks()
        assert len(locks) == 1
        assert locks[0].state["owner"] == "alice"
        assert locks[0].state["lock_scope"] == "project"
        assert locks[0].state["locked_at"] == first_locked_at # Preserved!

def test_conflicting_owner(tmp_path: Path) -> None:
    layout = PathLayout.for_workspace(tmp_path)
    context = RuntimeContext("home-1", layout, FakeClock(), FakeIdSource())
    with create_runtime(context) as runtime:
        service = runtime.file_lock_service
        
        service.lock_file(name="foo.txt", owner="alice")
        
        with pytest.raises(FileLockConflictError) as exc:
            service.lock_file(name="foo.txt", owner="bob")
        
        assert exc.value.current_owner == "alice"

def test_unstated_admin_override(tmp_path: Path) -> None:
    layout = PathLayout.for_workspace(tmp_path)
    context = RuntimeContext("home-1", layout, FakeClock(), FakeIdSource())
    with create_runtime(context) as runtime:
        service = runtime.file_lock_service
        
        service.lock_file(name="foo.txt", owner="alice")
        
        # Unlock with None owner force releases
        result = service.unlock_file(name="foo.txt", owner=None)
        assert result.disposition == FileUnlockDisposition.RELEASED
        
        assert len(service.list_active_locks()) == 0

def test_unlock_ownership_mismatch(tmp_path: Path) -> None:
    layout = PathLayout.for_workspace(tmp_path)
    context = RuntimeContext("home-1", layout, FakeClock(), FakeIdSource())
    with create_runtime(context) as runtime:
        service = runtime.file_lock_service
        
        service.lock_file(name="foo.txt", owner="alice")
        
        with pytest.raises(FileLockOwnershipMismatchError) as exc:
            service.unlock_file(name="foo.txt", owner="bob")
        
        assert exc.value.current_owner == "alice"
        assert exc.value.requested_owner == "bob"

def test_unlock_absent(tmp_path: Path) -> None:
    layout = PathLayout.for_workspace(tmp_path)
    context = RuntimeContext("home-1", layout, FakeClock(), FakeIdSource())
    with create_runtime(context) as runtime:
        service = runtime.file_lock_service
        
        result = service.unlock_file(name="absent.txt", owner="alice")
        assert result.disposition == FileUnlockDisposition.NOT_LOCKED

def test_legacy_translation_locks(tmp_path: Path) -> None:
    layout = PathLayout.for_workspace(tmp_path)
    context = RuntimeContext("home-1", layout, FakeClock(), FakeIdSource())
    with create_runtime(context) as runtime:
        from peerhub.client import Client
        from peerhub.core.ports import RequestContext
        api = runtime.application_api
        
        submission = SubmissionMetadata(
            client_request_id="req-1", correlation_id="corr-1", client_id="client-1",
            actor_id="test-actor", scope={}, idempotency_key="cmd-1",
            expected_policy_revision=None, expected_configuration_revision=None,
            client_timestamp=1000,
        )
        
        caller = RequestContext(principal="user-1", client_id="client-1")
        client = Client(api, caller=caller)
        
        # 1. Acquire
        acquire_cmd = LockAcquireCommand(
            submission=submission,
            name="test.txt",
            owner="alice",
            lock_scope="file",
        )
        client.submit(acquire_cmd)
        
        locks = runtime.file_lock_service.list_active_locks()
        assert len(locks) == 1
        assert locks[0].state["name"] == "test.txt"
        assert locks[0].state["owner"] == "alice"
        assert locks[0].state["lock_scope"] == "file"
        
        # 2. Status
        status_cmd = LockStatusCommand(submission=submission)
        status_result = client.submit(status_cmd)
        assert status_result.ok
        assert "items" in status_result.result
        assert len(status_result.result["items"]) == 1
        
        # 3. Release
        release_cmd = LockReleaseCommand(
            submission=submission,
            name="test.txt",
            owner="alice",
        )
        client.submit(release_cmd)
        
        locks = runtime.file_lock_service.list_active_locks()
        assert len(locks) == 0
