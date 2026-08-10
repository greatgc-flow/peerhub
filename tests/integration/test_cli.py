import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from peerhub.application.direct_ask import DirectAskResult
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.protocol import ErrorCode
from peerhub.dispatch.contract import RequestState
from peerhub.dispatch.capability import CapabilityTier
from peerhub.cli import UuidSource, main


def _ask_result(
    *,
    state: RequestState = RequestState.SUCCEEDED_VERIFIED,
    response_text: str | None = "hello from peer",
    error_code: ErrorCode | None = None,
    execution_certainty: ExecutionCertainty | None = None,
) -> DirectAskResult:
    return DirectAskResult(
        command_id="command-1",
        attempt_id="attempt-1",
        peer_kind="ag",
        profile_id="ag.standard",
        response_text=response_text,
        request_state=state,
        error_code=error_code,
        execution_certainty=execution_certainty,
    )


def test_cli_uuid_source_produces_domain_compatible_uuid4() -> None:
    value = UuidSource().new_id("outbox-event")

    assert str(uuid.UUID(value, version=4)) == value

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


def test_cli_ask_parses_all_arguments(tmp_path: Path, capsys) -> None:
    with patch(
        "peerhub.cli.execute_direct_ask",
        return_value=_ask_result(),
    ) as execute:
        exit_code = main(
            [
                "ask",
                "ag",
                "say hello",
                "--capability-tier",
                "WORKTREE_WRITE",
                "--workspace",
                str(tmp_path),
                "--profile",
                "ag.standard",
                "--timeout-seconds",
                "17",
                "--silence-timeout-seconds",
                "19",
                "--max-output-bytes",
                "12345",
            ]
        )

    assert exit_code == 0
    request = execute.call_args.args[0]
    assert request.peer_name == "ag"
    assert request.prompt == "say hello"
    assert request.required_capability_tier is CapabilityTier.WORKTREE_WRITE
    assert request.workspace_root == tmp_path.resolve()
    assert request.profile_id == "ag.standard"
    assert request.limits.process_timeout_ms == 17_000
    assert request.limits.silence_timeout_ms == 19_000
    assert request.limits.max_output_bytes == 12_345
    captured = capsys.readouterr()
    assert captured.out == "hello from peer\n"
    assert captured.err == ""


def test_cli_ask_requires_capability_tier() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["ask", "ag", "hello"])

    assert exc_info.value.code == 2


def test_cli_ask_unknown_peer_returns_usage_error(
    tmp_path: Path,
    capsys,
) -> None:
    with patch(
        "peerhub.cli.execute_direct_ask",
        side_effect=ValueError(
            "unsupported peer 'stranger'"
        ),
    ):
        exit_code = main(
            [
                "ask",
                "stranger",
                "hello",
                "--capability-tier",
                "READ_ONLY",
                "--workspace",
                str(tmp_path),
            ]
        )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == "peerhub ask: unsupported peer 'stranger'\n"


def test_cli_ask_json_output_has_stable_shape(
    tmp_path: Path,
    capsys,
) -> None:
    with patch(
        "peerhub.cli.execute_direct_ask",
        return_value=_ask_result(),
    ):
        exit_code = main(
            [
                "ask",
                "ag",
                "hello",
                "--capability-tier",
                "READ_ONLY",
                "--workspace",
                str(tmp_path),
                "--json",
            ]
        )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "command_id": "command-1",
        "attempt_id": "attempt-1",
        "peer_kind": "ag",
        "profile_id": "ag.standard",
        "response_text": "hello from peer",
        "request_state": "SUCCEEDED_VERIFIED",
        "error_code": None,
        "execution_certainty": None,
    }


@pytest.mark.parametrize(
    ("result", "expected_exit"),
    [
        (
            _ask_result(
                state=RequestState.FAILED,
                response_text=None,
                error_code=ErrorCode.PROTOCOL_ASSESSMENT_FAILED,
                execution_certainty=ExecutionCertainty.TERMINAL,
            ),
            3,
        ),
        (
            _ask_result(
                state=RequestState.INTERRUPTED,
                response_text=None,
                error_code=ErrorCode.PROCESS_TIMEOUT,
                execution_certainty=ExecutionCertainty.MAY_HAVE_STARTED,
            ),
            4,
        ),
    ],
)
def test_cli_ask_maps_returned_failure_states(
    tmp_path: Path,
    capsys,
    result: DirectAskResult,
    expected_exit: int,
) -> None:
    with patch(
        "peerhub.cli.execute_direct_ask",
        return_value=result,
    ):
        exit_code = main(
            [
                "ask",
                "ag",
                "hello",
                "--capability-tier",
                "READ_ONLY",
                "--workspace",
                str(tmp_path),
            ]
        )

    captured = capsys.readouterr()
    assert exit_code == expected_exit
    assert captured.out == ""
    assert captured.err == f"peerhub ask: {result.error_code.value}\n"


def test_cli_ask_keyboard_interrupt_is_honest(
    tmp_path: Path,
    capsys,
) -> None:
    with patch(
        "peerhub.cli.execute_direct_ask",
        side_effect=KeyboardInterrupt,
    ):
        exit_code = main(
            [
                "ask",
                "ag",
                "hello",
                "--capability-tier",
                "READ_ONLY",
                "--workspace",
                str(tmp_path),
            ]
        )

    captured = capsys.readouterr()
    assert exit_code == 130
    assert captured.out == ""
    assert "in-flight process may still be running" in captured.err
    assert "cancellation-ladder wiring is not yet implemented" in captured.err


@pytest.mark.slow
def test_cli_ask_real_agy_end_to_end(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    portable_root = Path(__file__).resolve().parents[3]
    agy_dir = portable_root / "_sys" / "tools" / "agy"
    agy_executable = agy_dir / "agy.exe"
    assert agy_executable.is_file()
    monkeypatch.setenv(
        "PATH",
        f"{agy_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    )

    exit_code = main(
        [
            "ask",
            "ag",
            "say hello in two words",
            "--capability-tier",
            "READ_ONLY",
            "--workspace",
            str(tmp_path),
            "--timeout-seconds",
            "180",
            "--silence-timeout-seconds",
            "180",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.strip()
    assert captured.err == ""
