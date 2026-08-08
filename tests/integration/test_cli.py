import sys
from pathlib import Path
from unittest.mock import patch
from peerhub.cli import main

def test_cli_status_uninitialized(tmp_path: Path, capsys):
    """Test 'peerhub status' against a fresh, uninitialized workspace."""
    exit_code = main(["status", "--workspace", str(tmp_path)])
    
    assert exit_code == 0
    captured = capsys.readouterr()
    stdout = captured.out
    
    assert f"Workspace: {tmp_path.resolve()}" in stdout
    assert "Workspace uninitialized (no database found)" in stdout

def test_cli_status_initialized(tmp_path: Path, capsys):
    """Test 'peerhub status' against an initialized workspace."""
    # Force initialize the DB first
    from peerhub.core.context import PathLayout, RuntimeContext
    from peerhub.cli import SystemClock, UuidSource
    from peerhub.runtime import create_runtime
    
    paths = PathLayout.for_workspace(tmp_path)
    context = RuntimeContext(
        workspace_home_id=tmp_path.name,
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )
    
    # create_runtime calls state_store.initialize() under the hood
    with create_runtime(context):
        pass
        
    # Now run status
    exit_code = main(["status", "--workspace", str(tmp_path)])
    
    assert exit_code == 0
    captured = capsys.readouterr()
    stdout = captured.out
    
    assert f"Workspace: {tmp_path.resolve()}" in stdout
    assert f"Database: {paths.database_path}" in stdout
    assert "Schema Migrations Applied: " in stdout
    assert "Health Circuit ('system'): (no listing API exists yet -- not queryable from the CLI)" in stdout
    assert "Active Leases: 0" in stdout
    assert "Status: OK" in stdout

def test_cli_status_with_lease(tmp_path, capsys):
    from peerhub.cli import main, SystemClock, UuidSource
    from peerhub.core.context import RuntimeContext, PathLayout
    from peerhub.runtime import create_runtime
    
    paths = PathLayout.for_workspace(tmp_path)
    context = RuntimeContext(
        workspace_home_id=tmp_path.name,
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )
    
    with create_runtime(context, adapter_peer_kind="fake") as runtime:
        runtime.state_store.initialize()
        
        from peerhub.dispatch.contract import LeaseSnapshot, LeaseState, LeaseFenceTuple, ProcessBirthIdentity
        from peerhub.core.protocol import CommandID
        
        fence = LeaseFenceTuple(
            session_id="sess-1",
            lease_id="lease-123",
            fencing_token=1,
            revision=42,
            owner_principal_id="principal-1",
            owner_instance_id="instance-1",
            owner_process_birth_identity=ProcessBirthIdentity(
                pid=9999,
                process_creation_time=1000,
            ),
            command_id=CommandID("cmd-123"),
            authority_epoch=1,
            attempt_id="att-1",
            owner_peer_id="peer-1",
        )
        lease = LeaseSnapshot(
            lease_id="lease-123",
            session_id="sess-1",
            fence=fence,
            state=LeaseState.ACTIVE,
            heartbeat_expires_at=2000,
            created_at=1000,
            updated_at=1500,
        )
        with runtime.state_store.unit_of_work() as uow:
            uow.add_lease(lease)
            uow.commit()
    
    import sys
    from unittest.mock import patch
    with patch.object(sys, "argv", ["peerhub", "status", "--workspace", str(tmp_path)]):
        main()
        
    captured = capsys.readouterr()
    stdout = captured.out
    
    assert "Active Leases: 1" in stdout
    assert "Status: OK" in stdout

def test_cli_version(capsys):
    """Test 'peerhub --version'."""
    with patch.object(sys, 'argv', ['peerhub', '--version']):
        try:
            # argparse's --version calls sys.exit()
            main()
        except SystemExit as e:
            assert e.code == 0
            
    captured = capsys.readouterr()
    assert "0.1.0" in captured.out
