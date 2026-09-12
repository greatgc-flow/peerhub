import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from peerhub.application.direct_ask import DirectAskResult
from peerhub.core.execution import ExecutionCertainty
from peerhub.core.identity import AuthenticatedSubject
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


def test_cli_implicit_state_command_never_initializes_current_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    exit_code = main(
        [
            "task",
            "create",
            "--task-id",
            "implicit-task",
            "--summary",
            "must not initialize",
            "--spec",
            "regression",
            "--creator",
            "tester",
        ]
    )

    assert exit_code == 2
    assert "explicit --workspace" in capsys.readouterr().err
    assert not (tmp_path / ".peerhub").exists()


def test_cli_explicit_workspace_allows_state_command_to_initialize(
    tmp_path: Path,
) -> None:
    exit_code = main(
        [
            "task",
            "create",
            "--workspace",
            str(tmp_path),
            "--task-id",
            "explicit-task",
            "--summary",
            "explicit initialization",
            "--spec",
            "regression",
            "--creator",
            "tester",
        ]
    )

    assert exit_code == 0
    assert (tmp_path / ".peerhub" / "peerhub.sqlite3").is_file()


def test_cli_initialized_workspace_keeps_implicit_command_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert main(["workspace", "init", "--workspace", str(tmp_path)]) == 0
    monkeypatch.chdir(tmp_path)

    exit_code = main(
        [
            "task",
            "create",
            "--task-id",
            "existing-task",
            "--summary",
            "existing workspace",
            "--spec",
            "regression",
            "--creator",
            "tester",
        ]
    )

    assert exit_code == 0


@pytest.mark.parametrize(
    "arguments",
    (
        ["task", "status", "--task-id", "missing"],
        ["consensus", "list"],
        ["peer", "status"],
        ["node", "list"],
        ["room", "status", "--room-id", "missing"],
    ),
)
def test_cli_read_only_inspection_never_initializes_workspace(
    arguments: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(arguments) == 0
    assert not (tmp_path / ".peerhub").exists()


def test_cli_workspace_init_is_explicit_initialization_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["workspace", "init"]) == 0
    assert (tmp_path / ".peerhub" / "peerhub.sqlite3").is_file()

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

def test_cli_config_paths_json_reports_every_family_and_source(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 4 (dotdir consolidation, ratified 2026-09-09): `peerhub config
    paths --json` is the acceptance instrument for Engram's env-var
    redirect -- every resolved root must be present with its source."""
    monkeypatch.delenv("PEERHUB_CONFIG_HOME", raising=False)
    exit_code = main(["config", "paths", "--workspace", str(tmp_path), "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)

    for name in (
        "global_config_home", "workspace_home", "workspace_config_home",
        "workspace_temp", "models_toml", "ask_toml", "arbiter_json",
        "proposals_json", "legacy_arbiter_json", "legacy_proposals_json",
    ):
        assert name in payload, f"{name} missing from config paths report"
        assert "path" in payload[name]
        assert "source" in payload[name]

    # Default (no PEERHUB_CONFIG_HOME set): global config home falls back
    # to ~/.peerhub/config, source "default".
    assert payload["global_config_home"]["source"] == "default"


def test_cli_config_paths_json_reports_env_source_when_redirected(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact acceptance check the ratified doc names: confirms
    PEERHUB_CONFIG_HOME resolving is reported as source "env", not
    silently defaulted -- this is how Engram's redirect (task 6) gets
    verified end-to-end without guessing."""
    redirected = tmp_path / ".engram" / "peerhub" / "config"
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(redirected))

    exit_code = main(["config", "paths", "--workspace", str(tmp_path), "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["global_config_home"]["source"] == "env"
    assert payload["global_config_home"]["path"] == str(redirected)


def test_cli_config_migrate_moves_valid_legacy_files_without_semantic_change(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from peerhub.application.arbiter_review import load_final_arbiter_policy
    from peerhub.application.proposals import load_proposal_voters

    legacy_home = tmp_path / ".peerhub"
    legacy_home.mkdir()
    arbiter_bytes = json.dumps(
        {
            "schema_version": 1,
            "enabled": True,
            "candidate": {"peer_name": "cc", "profile_id": "cc.effort"},
            "triggers": ["dissent"],
            "max_invocations": 3,
            "window_seconds": 900,
        },
        indent=2,
    ).encode("utf-8")
    proposals_bytes = json.dumps(
        {"schema_version": 1, "voters": ["cc", "cx"]}, indent=2
    ).encode("utf-8")
    (legacy_home / "arbiter.json").write_bytes(arbiter_bytes)
    (legacy_home / "proposals.json").write_bytes(proposals_bytes)
    before_arbiter = load_final_arbiter_policy(tmp_path)
    before_voters = load_proposal_voters(tmp_path)

    assert main(["config", "migrate", "--workspace", str(tmp_path)]) == 0

    output = capsys.readouterr().out
    assert "arbiter.json" in output
    assert "proposals.json" in output
    current_home = legacy_home / "config"
    assert not (legacy_home / "arbiter.json").exists()
    assert not (legacy_home / "proposals.json").exists()
    assert (current_home / "arbiter.json").read_bytes() == arbiter_bytes
    assert (current_home / "proposals.json").read_bytes() == proposals_bytes
    assert load_final_arbiter_policy(tmp_path) == before_arbiter
    assert load_proposal_voters(tmp_path) == before_voters


def test_cli_config_migrate_preflights_all_files_before_moving(
    tmp_path: Path,
) -> None:
    legacy_home = tmp_path / ".peerhub"
    legacy_home.mkdir()
    (legacy_home / "arbiter.json").write_text(
        json.dumps({"schema_version": 1}), encoding="utf-8"
    )
    invalid_proposals = legacy_home / "proposals.json"
    invalid_proposals.write_text(
        json.dumps({"schema_version": 1, "voters": "cc"}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="voters must be an array"):
        main(["config", "migrate", "--workspace", str(tmp_path)])

    assert (legacy_home / "arbiter.json").is_file()
    assert invalid_proposals.is_file()
    assert not (legacy_home / "config").exists()


def test_cli_config_migrate_rejects_old_and_new_conflict(
    tmp_path: Path,
) -> None:
    legacy_home = tmp_path / ".peerhub"
    current_home = legacy_home / "config"
    current_home.mkdir(parents=True)
    (legacy_home / "arbiter.json").write_text("{}", encoding="utf-8")
    (current_home / "arbiter.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match=r"both.*arbiter\.json.*config migrate"):
        main(["config", "migrate", "--workspace", str(tmp_path)])


def test_cli_config_validate_exits_zero_for_a_fresh_workspace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["config", "validate", "--workspace", str(tmp_path)])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "models.toml" in output
    assert "ask.toml" in output
    assert "arbiter.json" in output
    assert "proposals.json" in output


def test_cli_config_validate_exits_nonzero_for_a_malformed_layer(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".peerhub"
    config_dir.mkdir()
    (config_dir / "arbiter.json").write_text('{"enabled": true}', encoding="utf-8")

    exit_code = main(["config", "validate", "--workspace", str(tmp_path)])

    assert exit_code == 1


def test_cli_config_validate_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config", "validate", "--workspace", str(tmp_path), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert {entry["name"] for entry in payload} == {
        "models.toml",
        "ask.toml",
        "arbiter.json",
        "proposals.json",
    }


def test_cli_config_init_global_scope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    global_home = tmp_path / "global"
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(global_home))

    exit_code = main(["config", "init", "--scope", "global"])

    assert exit_code == 0
    assert (global_home / "ask.toml").is_file()
    assert (global_home / "models.toml").is_file()
    assert "initialized at" in capsys.readouterr().out


def test_cli_config_init_workspace_scope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["config", "init", "--scope", "workspace", "--workspace", str(tmp_path)])

    assert exit_code == 0
    assert (tmp_path / ".peerhub" / "config" / "ask.toml").is_file()
    assert not (tmp_path / ".peerhub" / "config" / "models.toml").exists()


def test_cli_backup_workspace_then_restore_round_trip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace_root = tmp_path / "workspace"
    output_dir = tmp_path / "backups"
    assert main(["workspace", "init", "--workspace", str(workspace_root)]) == 0
    capsys.readouterr()

    exit_code = main(
        [
            "backup",
            "workspace",
            "--workspace",
            str(workspace_root),
            "--output",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    backup_output = capsys.readouterr().out
    assert "Backup created:" in backup_output
    bundles = list(output_dir.iterdir())
    assert len(bundles) == 1
    bundle_dir = bundles[0]
    assert (bundle_dir / "peerhub.sqlite3").is_file()
    assert (bundle_dir / "MANIFEST.json").is_file()

    # Restoring the same workspace's own backup into itself is a legitimate
    # round trip: the detected target identity is exactly what was backed up.
    restore_exit_code = main(
        ["backup", "restore", str(bundle_dir), "--workspace", str(workspace_root)]
    )

    assert restore_exit_code == 0
    restore_output = capsys.readouterr().out
    assert "Restored workspace" in restore_output


def test_cli_backup_workspace_include_transcripts_flag_reaches_the_manifest(
    tmp_path: Path,
) -> None:
    """The transcript-inclusion decision is never implicit: the CLI flag
    must be traceable end-to-end into the bundle's own manifest, not just
    accepted and silently dropped somewhere in between."""

    from peerhub.application.backup import load_manifest

    workspace_root = tmp_path / "workspace"
    assert main(["workspace", "init", "--workspace", str(workspace_root)]) == 0

    without_flag = tmp_path / "backups-default"
    assert (
        main(
            [
                "backup",
                "workspace",
                "--workspace",
                str(workspace_root),
                "--output",
                str(without_flag),
            ]
        )
        == 0
    )
    assert load_manifest(next(without_flag.iterdir())).include_transcripts is False

    with_flag = tmp_path / "backups-explicit"
    assert (
        main(
            [
                "backup",
                "workspace",
                "--workspace",
                str(workspace_root),
                "--output",
                str(with_flag),
                "--include-transcripts",
            ]
        )
        == 0
    )
    assert load_manifest(next(with_flag.iterdir())).include_transcripts is True


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
        
        now = context.clock.now()
        
        fence = LeaseFenceTuple(
            session_id="sess-1",
            lease_id="lease-123",
            fencing_token=1,
            revision=42,
            owner_principal_id="principal-1",
            owner_instance_id="instance-1",
            owner_process_birth_identity=ProcessBirthIdentity(
                pid=9999,
                process_creation_time=now - 1000,
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
            heartbeat_expires_at=now + 60000,
            created_at=now - 1000,
            updated_at=now - 500,
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
    from importlib.metadata import version as installed_version

    with patch.object(sys, 'argv', ['peerhub', '--version']):
        try:
            # argparse's --version calls sys.exit()
            main()
        except SystemExit as e:
            assert e.code == 0

    captured = capsys.readouterr()
    # Compare against the actually-installed package version rather than a
    # hardcoded string, so this test doesn't go stale on every release.
    assert installed_version("peerhub") in captured.out


def test_package_dunder_version_matches_distribution_metadata():
    """peerhub.__version__ must never drift from the installed distribution
    version (regression guard for a real bug: __init__.py hardcoded "0.1.7"
    while pyproject.toml/dist metadata had already moved to 0.1.10, which
    made the telemetry dashboard silently show a stale version)."""
    from importlib.metadata import version as installed_version

    import peerhub

    assert peerhub.__version__ == installed_version("peerhub")

def test_cli_status_quota_table(tmp_path: Path, capsys) -> None:
    from peerhub.cli import main, SystemClock, UuidSource
    from peerhub.core.context import RuntimeContext, PathLayout
    from peerhub.runtime import create_runtime
    from peerhub.telemetry.contract import UsageProjectionSnapshot
    import sys
    from unittest.mock import patch
    
    paths = PathLayout.for_workspace(tmp_path)
    context = RuntimeContext(
        workspace_home_id=tmp_path.name,
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )
    
    with create_runtime(context, adapter_peer_kind="fake") as runtime:
        runtime.state_store.initialize()
        
        proj = UsageProjectionSnapshot(
            projection_id="proj-1",
            instance_id="peer-1",
            profile_id="prof-1",
            quota_pool_scope="session",
            used_fraction=0.25,
            remaining_fraction=0.75,
            window_started_at=100,
            resets_at=2000,
            revision=1,
            updated_at=150,
        )
        with runtime.state_store.unit_of_work() as uow:
            uow.add_usage_projection(proj)
            uow.commit()
    
    # Test --all
    with patch.object(sys, "argv", ["peerhub", "status", "--workspace", str(tmp_path), "--all"]):
        main()
        
    captured = capsys.readouterr()
    stdout = captured.out
    
    assert "PEER" in stdout
    assert "POOL" in stdout
    assert "USED%" in stdout
    assert "REMAINING%" in stdout
    assert "peer-1" in stdout
    assert "session" in stdout
    assert "25.0%" in stdout
    assert "75.0%" in stdout

    # Test --peer
    with patch.object(sys, "argv", ["peerhub", "status", "--workspace", str(tmp_path), "--peer", "unknown"]):
        main()
        
    captured = capsys.readouterr()
    stdout = captured.out
    
    assert "No quota data recorded yet" in stdout


def test_cli_bind_profile_then_model_status_round_trip(
    tmp_path: Path,
    capsys,
) -> None:
    bind_exit = main(
        [
            "node",
            "bind-profile",
            "--workspace",
            str(tmp_path),
            "--node-id",
            "cc",
            "--profile-id",
            "cc.standard",
            "--model-id",
            "claude-opus-test",
            "--reasoning-effort",
            "high",
            "--actor",
            "peer-1",
        ]
    )
    bind_output = capsys.readouterr()

    assert bind_exit == 0
    assert "Profile cc/cc.standard bound" in bind_output.out

    status_exit = main(
        [
            "node",
            "model-status",
            "--workspace",
            str(tmp_path),
        ]
    )
    status_output = capsys.readouterr()

    assert status_exit == 0
    assert (
        "peer\tstatus\tprofile\tmodel\teffort\tcost\tcontext\tcapabilities"
        in status_output.out
    )
    assert (
        "cc\tUNKNOWN\tcc.standard\tclaude-opus-test\thigh\t\t\t"
        in status_output.out
    )



def test_cli_ask_parses_all_arguments(tmp_path: Path, capsys) -> None:
    subject = AuthenticatedSubject(
        principal_id=r"local-cli:DOMAIN\alice",
        evidence_source="os-process-owner",
    )
    with (
        patch(
            "peerhub.cli.LocalProcessCallerIdentityProvider"
        ) as provider_type,
        patch(
            "peerhub.cli.execute_direct_ask",
            return_value=_ask_result(),
        ) as execute,
    ):
        provider_type.return_value.resolve.return_value = subject
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
    assert execute.call_args.kwargs["authenticated_subject"] == subject
    assert (
        execute.call_args.kwargs[
            "authenticated_subject"
        ].principal_id
        != "cli-user"
    )
    captured = capsys.readouterr()
    assert captured.out == "hello from peer\n"
    assert not captured.err or "[peerhub] initialized workspace" in captured.err


def test_cli_ask_defaults_capability_tier_to_read_only(tmp_path: Path) -> None:
    with (
        patch("peerhub.cli.LocalProcessCallerIdentityProvider") as provider_type,
        patch("peerhub.cli.execute_direct_ask", return_value=_ask_result()) as execute,
    ):
        provider_type.return_value.resolve.return_value = AuthenticatedSubject(
            principal_id="test", evidence_source="test"
        )
        exit_code = main(["ask", "ag", "hello", "--workspace", str(tmp_path)])
        assert exit_code == 0
        request = execute.call_args.args[0]
        assert request.required_capability_tier is CapabilityTier.READ_ONLY

def test_cli_ask_explicit_capability_tier_override(tmp_path: Path) -> None:
    with (
        patch("peerhub.cli.LocalProcessCallerIdentityProvider") as provider_type,
        patch("peerhub.cli.execute_direct_ask", return_value=_ask_result()) as execute,
    ):
        provider_type.return_value.resolve.return_value = AuthenticatedSubject(
            principal_id="test", evidence_source="test"
        )
        exit_code = main(["ask", "ag", "hello", "--capability-tier", "WORKTREE_WRITE", "--workspace", str(tmp_path)])
        assert exit_code == 0
        request = execute.call_args.args[0]
        assert request.required_capability_tier is CapabilityTier.WORKTREE_WRITE


def test_cli_ask_has_no_principal_override_flag() -> None:
    with patch("peerhub.cli.execute_direct_ask") as execute:
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "ask",
                    "ag",
                    "hello",
                    "--capability-tier",
                    "READ_ONLY",
                    "--principal",
                    "attacker-chosen",
                ]
            )

    assert exc_info.value.code == 2
    assert execute.call_count == 0


def test_cli_ask_fails_closed_without_local_identity(
    tmp_path: Path,
    capsys,
) -> None:
    with (
        patch(
            "peerhub.cli.LocalProcessCallerIdentityProvider"
        ) as provider_type,
        patch("peerhub.cli.execute_direct_ask") as execute,
    ):
        provider_type.return_value.resolve.return_value = None
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
    assert exit_code == 2
    assert execute.call_count == 0
    assert captured.out == ""
    assert captured.err == (
        "peerhub ask: local caller identity could not be verified\n"
    )


@pytest.mark.parametrize(
    "supplied_tier",
    [
        "SUPERUSER",
        "read_only",
        "READONLY",
        "0",
        "",
    ],
)
def test_cli_ask_rejects_a_capability_tier_outside_the_enum(
    supplied_tier: str,
) -> None:
    """The CLI accepts exact enum names only -- never a coerced near-miss.

    Increment 3 deliberately made ``--capability-tier`` an exact-choice flag
    with no prompt-text inference.  A value that merely *looks* like a tier
    (wrong case, the ordinal, the empty string) must exit 2 rather than be
    normalized into a grant, and ``execute_direct_ask`` must never be
    reached.
    """

    with patch("peerhub.cli.execute_direct_ask") as execute:
        with pytest.raises(SystemExit) as exc_info:
            main(["ask", "ag", "hello", "--capability-tier", supplied_tier])

    assert exc_info.value.code == 2
    assert execute.call_count == 0


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
    assert "peerhub ask: unsupported peer 'stranger'" in captured.err


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
    assert not captured.err or "[peerhub] initialized workspace" in captured.err
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
    assert f"peerhub ask: {result.error_code.value}" in captured.err


def test_cli_ask_keyboard_interrupt_cancels(
    tmp_path: Path,
    capsys,
) -> None:
    """KeyboardInterrupt now triggers the real cancellation ladder (T4),
    not just an honest "not implemented" message. `execute_direct_ask`
    raises inside the background dispatch thread here, so no live
    ProcessSupervisor ever reaches the cancellation hook -- the CLI must
    still exit 130 cleanly rather than hang waiting for one.
    """

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
    assert "cancelling in-flight process" in captured.err


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
    assert not captured.err or "[peerhub] initialized workspace" in captured.err


def test_cli_statusline_writes_no_log_file(tmp_path: Path, capsys, monkeypatch):
    """'peerhub statusline' only prints the formatted line (item 12,
    dotdir consolidation): it must not persist stdin to any durable log --
    neither the old .peerhub/ location nor a hardcoded Engram _sys/ path --
    since nothing in peerhub reads such a file (the real, consumed
    statusline log is written directly by the agy CLI's own hook)."""
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO('{"model": "Gemini 3.1 Pro"}'))

    exit_code = main(
        [
            "statusline",
            "--peer",
            "ag",
            "--workspace",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert not (tmp_path / ".peerhub" / "statusline").exists()
    assert not (tmp_path / "_sys").exists()

def test_cli_ask_auto_provision_notice(tmp_path, capsys):
    from peerhub.cli import main
    from unittest.mock import patch
    from peerhub.core.identity import AuthenticatedSubject
    
    with (
        patch("peerhub.cli.LocalProcessCallerIdentityProvider") as provider_type,
        patch("peerhub.cli.execute_direct_ask", return_value=_ask_result()) as execute,
    ):
        provider_type.return_value.resolve.return_value = AuthenticatedSubject(
            principal_id="test", evidence_source="test"
        )
        
        # First ask (auto-provisions)
        exit_code1 = main(["ask", "ag", "hello", "--workspace", str(tmp_path)])
        assert exit_code1 == 0
        captured1 = capsys.readouterr()
        assert "[peerhub] initialized workspace at " in captured1.err

        # Simulate creation
        from peerhub.core.context import PathLayout
        paths = PathLayout.for_workspace(tmp_path)
        paths.database_path.parent.mkdir(parents=True, exist_ok=True)
        paths.database_path.touch()

        # Second ask (already provisioned)

        # Second ask (already provisioned)
        exit_code2 = main(["ask", "ag", "hello", "--workspace", str(tmp_path)])
        assert exit_code2 == 0
        captured2 = capsys.readouterr()
        assert "[peerhub] initialized workspace at " not in captured2.err

def test_cli_python_m_peerhub(capsys):
    import subprocess
    import sys
    result = subprocess.run([sys.executable, "-m", "peerhub", "--version"], capture_output=True, text=True)
    assert result.returncode == 0
    
    # Compare with direct call
    from peerhub.cli import main
    with patch("sys.argv", ["peerhub", "--version"]):
        try:
            main()
        except SystemExit as e:
            assert e.code == 0
    captured = capsys.readouterr()
    
    assert captured.out.strip() == result.stdout.strip()

def test_cli_short_flags(tmp_path, capsys):
    from peerhub.cli import main
    from unittest.mock import patch
    from peerhub.core.identity import AuthenticatedSubject
    
    # Test ask short flags
    with (
        patch("peerhub.cli.LocalProcessCallerIdentityProvider") as provider_type,
        patch("peerhub.cli.execute_direct_ask", return_value=_ask_result()) as execute,
    ):
        provider_type.return_value.resolve.return_value = AuthenticatedSubject(
            principal_id="test", evidence_source="test"
        )
        # using -w, -t, -p, -j
        exit_code = main(["ask", "ag", "hello", "-w", str(tmp_path), "-t", "WORKTREE_WRITE", "-p", "ag.standard", "-j"])
        assert exit_code == 0
        req = execute.call_args.args[0]
        assert req.workspace_root == tmp_path.resolve()
        assert req.required_capability_tier is CapabilityTier.WORKTREE_WRITE
        assert req.profile_id == "ag.standard"
        captured = capsys.readouterr()
        import json
        assert json.loads(captured.out)
    
    # Test status short flags
        with patch("peerhub.cli._run_statusline") as run_statusline:
            main(["status", "-w", str(tmp_path)])
            capsys.readouterr() # clear stdout

        # Test diag short flags
    with patch("peerhub.cli._refresh_usage_projections", return_value=[]):
        exit_code = main(["diag", "-w", str(tmp_path), "-j"])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out)
        
    # Test broadcast short flags
    with patch("peerhub.cli.BroadcastCoordinator") as coordinator:
        from peerhub.application.broadcast import BroadcastResult
        coordinator.return_value.fan_out.return_value = BroadcastResult(round_id="1", disposition="all_completed", legs=[])
        exit_code = main(["broadcast", "ag", "hello", "-w", str(tmp_path), "-t", "READ_ONLY", "-j"])
        assert exit_code == 0
        req = coordinator.return_value.fan_out.call_args.args[0]
        assert req.workspace_root == tmp_path.resolve()
        assert req.required_capability_tier is CapabilityTier.READ_ONLY
        captured = capsys.readouterr()
        assert json.loads(captured.out)

