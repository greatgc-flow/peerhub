"""PeerHub CLI entry point and command dispatch."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, cast

from peerhub._console import tolerant_streams
from peerhub._exit_codes import INVALID_ARGUMENTS_EXIT
from peerhub._version import __version__
from peerhub.cli import account_cmd, ask_cmd, batch_cmd, core_cmd, diag_cmd, observation_cmd, profile_cmd, session_cmd
from peerhub.cli.arguments import ArgumentParser
from peerhub.cli.ask_cmd import with_writable
from peerhub.cli.explain_cmd import register_explain_parser, run_explain
from peerhub.cli.guide import register_guide_parser, render_guide, run_guide
from peerhub.cli.monitor import register_monitor_parser, run_monitor_command
from peerhub.cli.store_select import DEFAULT_DB_PATH, report_missing_store, select_store
from peerhub.cli.support import error_line
from peerhub.cli.update import register_update_parser, run_update
from peerhub.core.schema_version import SchemaVersionError
from peerhub.core.store import (
    CasMismatchError,
    IdempotencyConflictError,
    StorageCorruptError,
    StorageFullError,
    StorageReadOnlyError,
)

__all__ = ["build_parser", "main", "with_writable"]


def build_parser(prog: str = "peerhub") -> argparse.ArgumentParser:
    description = "PeerHub CLI"
    parser = ArgumentParser(prog=prog, description=description, epilog="Advanced: stream, record, offset. Start: AI = peerhub guide, peerhub diag --plan | human = peerhub monitor | first ask: peerhub ask cc \"prompt\" --profile cc.standard --stream ID --request-id R1 | bugs: peerhub explain report-issues")
    parser.add_argument("--version", "-v", "-V", action="version", version=f"peerhub {__version__}")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to SQLite database")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    core_cmd.register_core_parser(subparsers)
    ask_cmd.register_ask_parser(subparsers)
    account_cmd.register_account_parser(subparsers)
    batch_cmd.register_batch_parser(subparsers)
    profile_cmd.register_profile_parser(subparsers)
    ask_cmd.register_outcome_parser(subparsers)
    session_cmd.register_session_parser(subparsers)
    observation_cmd.register_observation_parser(subparsers)
    register_monitor_parser(subparsers)
    diag_cmd.register_diag_parser(subparsers)
    register_update_parser(subparsers)
    register_explain_parser(subparsers)
    register_guide_parser(subparsers)

    def add_json(node: argparse.ArgumentParser) -> None:
        if "--json" not in node._option_string_actions:
            node.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        node._option_string_actions["--json"].default = argparse.SUPPRESS
        node._option_string_actions["--json"].help = "Machine-readable JSON output"  # one text for every --json (#401)
        for action in node._actions:
            choices = cast(object, action.choices)
            if isinstance(choices, dict):
                for child in cast(dict[str, object], choices).values():
                    if isinstance(child, argparse.ArgumentParser):
                        add_json(child)
    subparsers._choices_actions[:] = [action for action in subparsers._choices_actions
                                     if action.dest not in {"stream", "record", "offset"}]
    add_json(parser)
    parser.set_defaults(json=False)
    return parser


def _dispatch_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    match args.subcommand:
        case "ask":
            return ask_cmd.run_ask(args)
        case "account":
            return account_cmd.run_account(args)
        case "batch":
            return batch_cmd.run_batch(args)
        case "profile":
            return profile_cmd.run_profile(args)
        case "outcome":
            return ask_cmd.run_outcome(args)
        case "session":
            return session_cmd.run_session(args)
        case "monitor":
            return run_monitor_command(args, parser)
        case "observation":
            return observation_cmd.run_observation(args)
        case "diag":
            return diag_cmd.run_diag(args, parser)
        case "update":
            return run_update(args)
        case "explain":
            return run_explain(args)
        case "guide":
            return run_guide(args)
        case _:
            return core_cmd.run_core(args)


def main(argv: list[str] | None = None, prog: str = "peerhub") -> int:
    tolerant_streams()
    parser = build_parser(prog=prog)
    actual = sys.argv[1:] if argv is None else argv
    if not actual:
        print(render_guide())
        return 0
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code == INVALID_ARGUMENTS_EXIT and "ask" in actual and "--json" in actual:
            return ask_cmd.report_ask_parse_error(exc)
        raise

    def error_output(code: str, message: str, session: dict[str, Any] | None = None, exception: BaseException | None = None) -> None:
        if args.subcommand == "ask":
            ask_cmd.report_ask_error(args, actual, code, message, session, exception)
        else:
            error_line(code, message)

    explicit_db = any(a == "--db" or a.startswith("--db=") for a in actual)
    try:
        # Pure ask usage errors must win over store discovery and missing stores.
        if args.subcommand == "ask":
            ask_cmd.validate_ask_args(args, parser)
        if hasattr(args, "db") and args.db and not getattr(args, "update_check", False):
            workspace = Path(args.workspace) if args.subcommand in ("ask", "profile") and getattr(args, "workspace", None) else None
            if args.subcommand == "ask" and args.dry_run:
                selection = select_store(args.db, discover=not explicit_db, workspace_root=workspace, allow_process=False)
            else:
                selection = select_store(args.db, discover=not explicit_db, workspace_root=workspace)
            args.db, args.db_source = selection.path, selection.source
        if getattr(args, "json", False) and hasattr(args, "format"):
            args.format = "json"
        # Configuration/help commands remain usable without a store. Store consumers
        # must never turn an implicit discovery miss into a new database.
        store_command = args.subcommand in {
            "ask", "account", "session", "observation",
            "peer", "stream", "record", "offset", "collection",
        } or (args.subcommand == "monitor" and getattr(args, "monitor_action", None) in (None, "repair"))
        # Diag owns its read-only missing-store reports; batch validates its
        # manifest and supports dry runs before checking the execution store.
        if store_command and getattr(args, "db_source", None) not in ("explicit", "env"):
            if report_missing_store(args.db, json_output=bool(getattr(args, "json", False) or getattr(args, "format", None) == "json"),
                                    selected=False, source=args.db_source):
                return 5
        if args.subcommand == "monitor":
            args._provided_options = {item.split("=", 1)[0] for item in actual if item.startswith("--")}
        if getattr(args, "obs_db", None):
            args.obs_db = select_store(args.obs_db, use_env=False, discover=False).path
        return _dispatch_command(args, parser)

    except (StorageReadOnlyError, StorageFullError, StorageCorruptError) as e:
        error_output("storage_corrupt", f"STORAGE ERROR ({type(e).__name__}): {e}", getattr(e, "session", None), exception=e)
        return 4
    except SchemaVersionError as e:
        error_output("schema_version_error", f"SCHEMA VERSION ERROR: {e}", exception=e)
        return 6
    except IdempotencyConflictError as e:
        error_output("idempotency_conflict", f"CONFLICT ERROR: {e}", exception=e)
        return 2
    except CasMismatchError as e:
        error_output("cas_conflict", f"CAS ERROR: {e}", exception=e)
        return 3
    except ValueError as e:
        code = getattr(e, "code", "invalid_arguments")
        message = str(e) if code == "peer_not_found" else f"ERROR: {e}"
        error_output(code, message, exception=e)
        if code == "invalid_arguments" or (args.subcommand == "ask" and code not in ("session_lost", "peer_not_found")):
            return INVALID_ARGUMENTS_EXIT
        return 1
    except SystemExit as e:
        if args.subcommand == "ask" and args.json and e.code == INVALID_ARGUMENTS_EXIT:
            return ask_cmd.report_ask_validation_error(e)
        raise
    except Exception as e:
        error_output("delivery_failed", f"ERROR: {e}", getattr(e, "session", None), exception=e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
