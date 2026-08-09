"""Command-line interface for PeerHub."""

import argparse
import json
import sqlite3
import sys
import time
import uuid
from importlib.metadata import version
from pathlib import Path

from peerhub.adapters.registry import (
    ExecutableNotFoundError,
    ProfileNotFoundError,
)
from peerhub.application.bootstrap import (
    HealthPolicyConflictError,
    ReadinessProbeFailedError,
)
from peerhub.application.direct_ask import (
    DirectAskRequest,
    DirectAskResult,
    execute_direct_ask,
)
from peerhub.core.context import Clock, IdSource, PathLayout, RuntimeContext
from peerhub.core.execution import ExecutionCertainty, TransportLimits
from peerhub.dispatch.contract import RequestState
from peerhub.runtime import create_runtime

class SystemClock(Clock):
    """Real system clock for production use."""
    def now(self) -> int:
        return int(time.time())

class UuidSource(IdSource):
    """Real UUID source for production use."""
    def new_id(self, namespace: str) -> str:
        del namespace
        return str(uuid.uuid4())


def _ask_exit_code(result: DirectAskResult) -> int:
    """Map direct-ask evidence to the stable CLI exit-code contract."""

    if (
        result.request_state is RequestState.SUCCEEDED_VERIFIED
        and result.response_text is not None
        and result.response_text.strip()
    ):
        return 0

    if result.execution_certainty is ExecutionCertainty.NOT_STARTED:
        return 2
    if result.execution_certainty in (
        ExecutionCertainty.MAY_HAVE_STARTED,
        ExecutionCertainty.STARTED,
    ):
        return 4

    if result.request_state in (
        RequestState.RECEIVED,
        RequestState.REJECTED_VALIDATION,
        RequestState.ADMITTED,
        RequestState.REJECTED_POLICY,
        RequestState.PREPARED,
        RequestState.FAILED_PRE_DISPATCH,
    ):
        return 2
    if result.request_state in (
        RequestState.DISPATCH_INTENT,
        RequestState.START_UNCERTAIN,
        RequestState.RUNNING,
        RequestState.CANCELLING,
        RequestState.ASSESSING,
        RequestState.INTERRUPTED,
        RequestState.CANCELLED,
    ):
        return 4
    return 3


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def _print_ask_json(result: DirectAskResult) -> None:
    print(
        json.dumps(
            {
                "command_id": result.command_id,
                "attempt_id": result.attempt_id,
                "peer_kind": result.peer_kind,
                "profile_id": result.profile_id,
                "response_text": result.response_text,
                "request_state": _enum_value(result.request_state),
                "error_code": _enum_value(result.error_code),
                "execution_certainty": _enum_value(
                    result.execution_certainty
                ),
            },
            ensure_ascii=False,
        )
    )


def _run_ask(parsed: argparse.Namespace) -> int:
    try:
        request = DirectAskRequest(
            workspace_root=Path(parsed.workspace).resolve(),
            peer_name=parsed.peer,
            prompt=parsed.prompt,
            profile_id=parsed.profile,
            limits=TransportLimits(
                process_timeout_ms=parsed.timeout_seconds * 1000,
                silence_timeout_ms=(
                    parsed.silence_timeout_seconds * 1000
                ),
                max_output_bytes=parsed.max_output_bytes,
            ),
        )
        result = execute_direct_ask(
            request,
            clock=SystemClock(),
            ids=UuidSource(),
        )
    except KeyboardInterrupt:
        # TODO(Phase 3 increment 5): dispatch_and_execute() constructs its
        # ProcessSupervisor internally (workflows.py:686), so the CLI has no
        # live cancellation handle. Add a supervisor/cancellation hook there,
        # then route Ctrl-C through ProcessSupervisor.begin_cancellation() and
        # its SOFT_CANCEL -> TERMINATE_TREE -> KILL_TREE cleanup ladder.
        print(
            "peerhub ask: interrupt received; the in-flight process may "
            "still be running because cancellation-ladder wiring is not "
            "yet implemented",
            file=sys.stderr,
        )
        return 130
    except (
        ValueError,
        ProfileNotFoundError,
        ExecutableNotFoundError,
        ReadinessProbeFailedError,
        HealthPolicyConflictError,
        RuntimeError,
        OSError,
        sqlite3.Error,
    ) as error:
        print(f"peerhub ask: {error}", file=sys.stderr)
        return 2

    exit_code = _ask_exit_code(result)
    if parsed.json:
        _print_ask_json(result)
    elif exit_code == 0:
        assert result.response_text is not None
        sys.stdout.write(result.response_text)
        if not result.response_text.endswith("\n"):
            sys.stdout.write("\n")

    if exit_code != 0:
        detail = result.error_code or result.request_state or "unknown failure"
        print(f"peerhub ask: {_enum_value(detail)}", file=sys.stderr)
    return exit_code

def main(args: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PeerHub Local Coordination CLI")
    parser.add_argument("--version", action="version", version=version("peerhub"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    status_parser = subparsers.add_parser("status", help="Show the current workspace status")
    status_parser.add_argument(
        "--workspace", 
        default=".", 
        help="Path to the workspace root (default: current directory)"
    )

    ask_parser = subparsers.add_parser(
        "ask",
        help="Send one prompt to a real peer CLI",
    )
    ask_parser.add_argument(
        "peer",
        help="Peer name (ag/agy, cc/claude, cx/codex)",
    )
    ask_parser.add_argument("prompt", help="Prompt text to send")
    ask_parser.add_argument(
        "--workspace",
        default=".",
        help="Path to the workspace root (default: current directory)",
    )
    ask_parser.add_argument(
        "--profile",
        default=None,
        help="Explicit profile ID",
    )
    ask_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=60,
    )
    ask_parser.add_argument(
        "--silence-timeout-seconds",
        type=int,
        default=60,
    )
    ask_parser.add_argument(
        "--max-output-bytes",
        type=int,
        default=1_000_000,
    )
    ask_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON",
    )
    
    parsed = parser.parse_args(args)
    
    if parsed.command == "status":
        workspace_root = Path(parsed.workspace).resolve()
        paths = PathLayout.for_workspace(workspace_root)
        
        print(f"Workspace: {workspace_root}")
        print(f"Database: {paths.database_path}")
        
        if not paths.database_path.exists():
            print("Status: Workspace uninitialized (no database found)")
            return 0
            
        context = RuntimeContext(
            workspace_home_id=workspace_root.name,
            paths=paths,
            clock=SystemClock(),
            ids=UuidSource(),
        )
        
        # create_runtime will construct all services
        with create_runtime(context, adapter_peer_kind="fake") as runtime:
            # Check migrations
            conn = runtime.state_store._connect()  # pyright: ignore[reportPrivateUsage]
            try:
                migrations = runtime.state_store._migration_versions(conn)  # pyright: ignore[reportPrivateUsage]
                print(f"Schema Migrations Applied: {len(migrations)}")
            except sqlite3.OperationalError:
                print("Schema Migrations Applied: 0 (table missing)")
            finally:
                conn.close()
                
            # Health circuit
            print("Health Circuit ('system'): (no listing API exists yet -- not queryable from the CLI)")
            
            # Active leases
            active_leases = runtime.dispatch_service.count_active_leases()
            print(f"Active Leases: {active_leases}")
            print("Status: OK")

    if parsed.command == "ask":
        return _run_ask(parsed)
            
    return 0

if __name__ == "__main__":
    sys.exit(main())
