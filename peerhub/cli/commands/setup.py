"""Setup and maintenance command registrations and translators."""

from __future__ import annotations

import argparse
from pathlib import Path
from types import ModuleType

from peerhub.cli.context import resolve_workspace


def register_workspace_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],  # pyright: ignore[reportPrivateUsage]
) -> None:
    """Register the stable workspace command at its established order."""

    workspace_parser = subparsers.add_parser(
        "workspace", help="Manage the peerhub workspace itself"
    )
    workspace_subparsers = workspace_parser.add_subparsers(
        dest="workspace_action", required=True
    )
    workspace_init_parser = workspace_subparsers.add_parser(
        "init",
        help="Explicitly initialize a workspace (creates .peerhub/peerhub.sqlite3)",
    )
    workspace_init_parser.add_argument(
        "--workspace",
        default=None,
        help="Path to the workspace root (default: current directory)",
    )


def register_setup_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],  # pyright: ignore[reportPrivateUsage]
) -> None:
    """Register the stable config, backup, and adapter command surface."""

    config_parser = subparsers.add_parser(
        "config", help="Inspect peerhub's own resolved configuration"
    )
    config_subparsers = config_parser.add_subparsers(
        dest="config_command", required=True
    )
    config_paths_parser = config_subparsers.add_parser(
        "paths", help="Report every resolved config path and its source"
    )
    config_paths_parser.add_argument(
        "--workspace",
        default=None,
        help="Path to the workspace root (default: current directory)",
    )
    config_paths_parser.add_argument("--json", action="store_true", help="Emit JSON output")
    config_migrate_parser = config_subparsers.add_parser(
        "migrate",
        help="Move legacy .peerhub/{arbiter,proposals}.json to the config/ tier (item 8)",
    )
    config_migrate_parser.add_argument(
        "--workspace",
        default=None,
        help="Path to the workspace root (default: current directory)",
    )
    config_validate_parser = config_subparsers.add_parser(
        "validate", help="Validate every resolved config layer and report diagnostics"
    )
    config_validate_parser.add_argument(
        "--workspace",
        default=None,
        help="Path to the workspace root (default: current directory)",
    )
    config_validate_parser.add_argument(
        "--json", action="store_true", help="Emit JSON output"
    )
    config_init_parser = config_subparsers.add_parser(
        "init", help="Scaffold the config/ directory for one scope, seeding starter files"
    )
    config_init_parser.add_argument(
        "--scope",
        choices=("global", "workspace"),
        required=True,
        help="Which config tier to initialize",
    )
    config_init_parser.add_argument(
        "--workspace",
        default=None,
        help="Path to the workspace root (used with --scope workspace)",
    )

    backup_parser = subparsers.add_parser(
        "backup", help="Back up or restore one workspace"
    )
    backup_subparsers = backup_parser.add_subparsers(
        dest="backup_command", required=True
    )
    backup_workspace_parser = backup_subparsers.add_parser(
        "workspace", help="Create a backup bundle (live SQLite snapshot + config/ files)"
    )
    backup_workspace_parser.add_argument(
        "--workspace",
        default=None,
        help="Path to the workspace root (default: current directory)",
    )
    backup_workspace_parser.add_argument(
        "--output", required=True, help="Directory to create the backup bundle under"
    )
    backup_workspace_parser.add_argument(
        "--include-transcripts",
        action="store_true",
        help="Include dispatch_transcripts rows (durable, sensitive dispatch text; omitted by default)",
    )
    backup_restore_parser = backup_subparsers.add_parser(
        "restore", help="Restore a backup bundle into a workspace"
    )
    backup_restore_parser.add_argument("bundle", help="Path to the backup bundle directory")
    backup_restore_parser.add_argument(
        "--workspace",
        default=None,
        help="Path to the workspace root (default: current directory)",
    )

    adapter_parser = subparsers.add_parser("adapter", help="Manage peerhub adapters")
    adapter_subparsers = adapter_parser.add_subparsers(
        dest="adapter_command", required=True
    )
    adapter_discover_parser = adapter_subparsers.add_parser(
        "discover", help="Discover installed built-in adapters"
    )
    adapter_discover_parser.add_argument("--json", action="store_true", help="Emit JSON output")


def run_setup_command(parsed: argparse.Namespace, cli: ModuleType) -> int | None:
    """Translate one setup command using the legacy module's patchable seams."""

    if parsed.command == "workspace" and parsed.workspace_action == "init":
        workspace_root = resolve_workspace(parsed.workspace).root
        paths = cli.PathLayout.for_workspace(workspace_root)
        already_initialized = paths.database_path.exists()
        context = cli.RuntimeContext(
            workspace_home_id=cli._detect_workspace_home_id(
                paths.database_path, workspace_root.name
            ),
            paths=paths,
            clock=cli.SystemClock(),
            ids=cli.UuidSource(),
        )
        with cli.create_runtime(context, adapter_peer_kind="fake"):
            pass
        if already_initialized:
            print(f"Workspace already initialized: {workspace_root}")
        else:
            print(f"Workspace initialized: {workspace_root}")
        return 0

    if parsed.command == "config" and parsed.config_command == "paths":
        workspace_root = resolve_workspace(parsed.workspace).root
        resolved = cli.resolve_config_paths(workspace_root=workspace_root)
        payload = resolved.as_dict()
        if parsed.json:
            print(cli.json.dumps(payload, indent=2))
        else:
            for name, entry in payload.items():
                print(f"{name}: {entry['path']} (source: {entry['source']})")
        return 0

    if parsed.command == "config" and parsed.config_command == "migrate":
        return run_config_migrate(parsed, cli)
    if parsed.command == "config" and parsed.config_command == "validate":
        return run_config_validate(parsed, cli)
    if parsed.command == "config" and parsed.config_command == "init":
        return run_config_init(parsed, cli)
    if parsed.command == "backup" and parsed.backup_command == "workspace":
        return run_backup_workspace(parsed, cli)
    if parsed.command == "backup" and parsed.backup_command == "restore":
        return run_backup_restore(parsed, cli)
    if parsed.command == "adapter":
        return run_adapter(parsed, cli)
    return None


def run_config_migrate(parsed: argparse.Namespace, cli: ModuleType) -> int:
    """Move validated legacy configuration files into the config tier."""

    workspace_root = resolve_workspace(parsed.workspace).root
    resolved = cli.resolve_config_paths(workspace_root=workspace_root)
    candidates = (
        (resolved.legacy_arbiter_json.path, resolved.arbiter_json.path, cli.load_final_arbiter_policy),
        (resolved.legacy_proposals_json.path, resolved.proposals_json.path, cli.load_proposal_voters),
    )
    to_move: list[tuple[Path, Path]] = []
    for legacy_path, new_path, loader in candidates:
        if not legacy_path.is_file():
            continue
        loader(workspace_root)
        to_move.append((legacy_path, new_path))
    for legacy_path, new_path in to_move:
        new_path.parent.mkdir(parents=True, exist_ok=True)
        cli.shutil.move(str(legacy_path), str(new_path))
        print(f"Migrated {legacy_path.name} -> {new_path}")
    return 0


def run_config_validate(parsed: argparse.Namespace, cli: ModuleType) -> int:
    from peerhub.application.config_validate import validate_workspace_config

    reports = validate_workspace_config(cli.Path(parsed.workspace).resolve())
    if parsed.json:
        print(cli.json.dumps([report.as_dict() for report in reports], indent=2))
    else:
        for report in reports:
            tag = "OK" if report.ok else "ERROR"
            print(f"[{tag:>5}] {report.name}: {report.detail}")
            for key, layer in sorted(report.winning_layer.items()):
                print(f"          {key} <- {layer}")
    return 0 if all(report.ok for report in reports) else 1


def run_config_init(parsed: argparse.Namespace, cli: ModuleType) -> int:
    from peerhub.application.config_init import init_config_scope

    workspace_root = resolve_workspace(parsed.workspace).root if parsed.scope == "workspace" else None
    config_home, created = init_config_scope(scope=parsed.scope, workspace_root=workspace_root)
    print(f"Config scope {parsed.scope!r} initialized at: {config_home}")
    for name in created:
        print(f"  created {name}")
    if not created:
        print("  (no new starter files -- all already present)")
    return 0


def run_backup_workspace(parsed: argparse.Namespace, cli: ModuleType) -> int:
    from datetime import datetime, timezone

    from peerhub.application.backup import create_workspace_backup

    workspace_root = resolve_workspace(parsed.workspace).root
    output_dir = cli.Path(parsed.output).resolve()
    bundle_dir = create_workspace_backup(
        workspace_root,
        output_dir=output_dir,
        include_transcripts=parsed.include_transcripts,
        now=datetime.now(timezone.utc).isoformat(),
    )
    print(f"Backup created: {bundle_dir}")
    return 0


def run_backup_restore(parsed: argparse.Namespace, cli: ModuleType) -> int:
    from peerhub.application.backup import restore_workspace_backup

    workspace_root = resolve_workspace(parsed.workspace).root
    bundle_dir = cli.Path(parsed.bundle).resolve()
    manifest = restore_workspace_backup(bundle_dir, workspace_root=workspace_root)
    print(f"Restored workspace {manifest.workspace_home_id!r} from {bundle_dir}")
    return 0


def run_adapter(parsed: argparse.Namespace, cli: ModuleType) -> int:
    if getattr(parsed, "adapter_command", None) == "discover":
        from peerhub.adapters.discovery import (
            AdapterFoundAndReady,
            AdapterNotReady,
            discover_builtin_adapters,
        )

        results = discover_builtin_adapters()
        if parsed.json:
            output: dict[str, object] = {}
            for result in results:
                if isinstance(result, AdapterFoundAndReady):
                    output[result.peer_kind] = {"state": "MEASURED", "executable_path": str(result.executable_path), "profiles": result.profiles}
                elif isinstance(result, AdapterNotReady):
                    output[result.peer_kind] = {"state": "UNAVAILABLE", "executable_path": str(result.executable_path), "reason": result.reason}
                else:
                    output[result.peer_kind] = {"state": "ABSENT"}
            print(cli.json.dumps(cli._json_safe(output), indent=2))
        else:
            for result in results:
                if isinstance(result, AdapterFoundAndReady):
                    print(f"[{result.peer_kind}] FOUND (ready): {result.executable_path} (profiles: {', '.join(result.profiles)})")
                elif isinstance(result, AdapterNotReady):
                    print(f"[{result.peer_kind}] UNAVAILABLE: {result.executable_path} - {result.reason}")
                else:
                    print(f"[{result.peer_kind}] ABSENT: not found in PATH")
        return 0
    print("error: missing adapter subcommand", file=cli.sys.stderr)
    return 2
