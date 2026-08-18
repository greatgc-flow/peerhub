"""Command-line interface for PeerHub."""

import argparse
import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from peerhub.persistence.sqlite import SqliteReadUnitOfWork

from peerhub.adapters.registry import (
    ExecutableNotFoundError,
    ProfileNotFoundError,
    resolve_peer_target,
)
from peerhub.application.bootstrap import (
    HealthPolicyConflictError,
    ReadinessProbeFailedError,
    build_broadcast_admission_config,
    build_direct_ask_admission_config,
)
from peerhub.application.direct_ask import (
    DirectAskRequest,
    DirectAskResult,
    execute_direct_ask,
)
from peerhub.core.context import Clock, IdSource, PathLayout, RuntimeContext
from peerhub.core.execution import ExecutionCertainty, TransportLimits
from peerhub.core.identity import (
    CallerIdentityProvider,
    LocalProcessCallerIdentityProvider,
    require_caller_identity,
)
from peerhub.dispatch.contract import RequestState
from peerhub.dispatch.capability import CapabilityTier
from peerhub.dispatch.process import ProcessSupervisor
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


def _run_ask(
    parsed: argparse.Namespace,
    *,
    caller_identity_provider: CallerIdentityProvider | None = None,
) -> int:
    class _AskState:
        supervisor: ProcessSupervisor | None = None
        result: DirectAskResult | None = None
        error: BaseException | None = None

    state = _AskState()
    done = threading.Event()
    thread_started = False

    try:
        authenticated_subject = require_caller_identity(
            caller_identity_provider
            if caller_identity_provider is not None
            else LocalProcessCallerIdentityProvider()
        )
        request = DirectAskRequest(
            workspace_root=Path(parsed.workspace).resolve(),
            peer_name=parsed.peer,
            prompt=parsed.prompt,
            required_capability_tier=CapabilityTier[
                parsed.capability_tier
            ],
            profile_id=parsed.profile,
            limits=TransportLimits(
                process_timeout_ms=parsed.timeout_seconds * 1000,
                silence_timeout_ms=(
                    parsed.silence_timeout_seconds * 1000
                ),
                max_output_bytes=parsed.max_output_bytes,
            ),
        )

        def _cancellation_hook(sup: ProcessSupervisor) -> None:
            state.supervisor = sup

        def _run_ask_thread() -> None:
            try:
                state.result = execute_direct_ask(
                    request,
                    clock=SystemClock(),
                    ids=UuidSource(),
                    authenticated_subject=authenticated_subject,
                    cancellation_hook=_cancellation_hook,
                )
            except BaseException as e:
                state.error = e
            finally:
                done.set()

        t = threading.Thread(target=_run_ask_thread, name="PeerhubDirectAsk")
        thread_started = True
        t.start()

        # Wait on the main thread so that KeyboardInterrupt can be raised cleanly here
        while not done.wait(0.1):
            pass

        if state.error is not None:
            raise state.error

        result = state.result
        assert result is not None
    except KeyboardInterrupt:
        print(
            "\npeerhub ask: interrupt received; cancelling in-flight process...",
            file=sys.stderr,
        )
        if thread_started:
            # Poll for a short window to see if supervisor becomes available
            # (in case the interrupt hit before the thread fully started the dispatch)
            for _ in range(20):
                if state.supervisor is not None:
                    break
                time.sleep(0.05)
            
            if state.supervisor is not None:
                state.supervisor.begin_cancellation()
            done.wait()
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

def _print_quota_table(uow: "SqliteReadUnitOfWork", peer: str | None) -> None:
    projections = uow.list_usage_projections(peer)
    if not projections:
        print("No quota data recorded yet")
        return

    print(f"\n{'PEER':<10} {'POOL':<30} {'USED%':<10} {'REMAINING%':<15} {'RESETS_AT'}")
    for p in projections:
        resets_str = datetime.fromtimestamp(p.resets_at, tz=timezone.utc).isoformat() if p.resets_at else "N/A"
        print(f"{p.instance_id:<10} {p.quota_pool_scope:<30} {p.used_fraction * 100:>5.1f}%    {p.remaining_fraction * 100:>9.1f}%      {resets_str}")

def _run_diag(parsed: argparse.Namespace) -> int:
    from peerhub.telemetry.presenter import TelemetryPresenter
    workspace_root = Path(parsed.workspace).resolve()
    presenter = TelemetryPresenter(
        use_color=False if parsed.no_color else None,
        workspace_root=workspace_root,
    )
    if parsed.live:
        try:
            import msvcrt
            has_msvcrt = True
        except ImportError:
            has_msvcrt = False

        try:
            while True:
                if os.name == "nt":
                    os.system("cls")
                else:
                    sys.stdout.write("\033[2J\033[H")
                    sys.stdout.flush()

                snapshot = presenter.collect_live_snapshot()
                if parsed.json:
                    print(json.dumps(snapshot, indent=2))
                else:
                    rendered = presenter.render(snapshot)
                    print(rendered)
                    print(presenter._c(" [Live Monitor Active: Press ESC or 'q' to exit]", "dim"))

                # Poll for key hit in 0.05s steps (total 2.0s refresh interval)
                total_interval = 2.0
                step = 0.05
                elapsed = 0.0
                while elapsed < total_interval:
                    if has_msvcrt and msvcrt.kbhit():
                        ch = msvcrt.getch()
                        if ch in (b"\x1b", b"q", b"Q", b"\x03"):  # ESC, q, Q, Ctrl+C
                            return 0
                    time.sleep(step)
                    elapsed += step
        except KeyboardInterrupt:
            return 0
        return 0
    else:
        snapshot = presenter.collect_live_snapshot()
        if parsed.json:
            print(json.dumps(snapshot, indent=2))
        else:
            print(presenter.render(snapshot))
        return 0


def _detect_workspace_home_id(database_path: Path, fallback_name: str) -> str:
    """Read the persisted workspace identity, falling back to the directory name."""
    if database_path.exists():
        try:
            conn = sqlite3.connect(str(database_path))
            try:
                row = conn.execute(
                    "SELECT workspace_home_id FROM workspace_identity WHERE singleton = 1"
                ).fetchone()
                if row and row[0]:
                    return str(row[0])
            finally:
                conn.close()
        except sqlite3.Error:
            pass
    return fallback_name or "cli"


def _run_statusline(parsed: argparse.Namespace) -> int:
    from peerhub.telemetry.statusline import format_statusline_ag
    stdin_data = ""
    if not sys.stdin.isatty():
        try:
            stdin_data = sys.stdin.read()
        except Exception:
            pass

    # Save to status log if sys_dir exists
    workspace_root = Path(parsed.workspace).resolve()
    log_dest = workspace_root / "_sys" / "data" / "temp" / "ag_statusline_stdin.log"
    if stdin_data:
        try:
            log_dest.parent.mkdir(parents=True, exist_ok=True)
            log_dest.write_text(stdin_data, encoding="utf-8")
        except OSError:
            pass

    peer = getattr(parsed, "peer", "ag")
    try:
        if peer == "ag":
            print(format_statusline_ag(stdin_data), end="")
        else:
            print(format_statusline_ag(stdin_data), end="")
    except Exception:
        print("ag:Gemini | ctx:ok | hub:idle [room-efde]", end="")
    return 0


def _run_broadcast(parsed: argparse.Namespace) -> int:
    from peerhub.application.broadcast import BroadcastCoordinator, FanOutRequest
    from peerhub.dispatch.capability import CapabilityTier
    workspace_root = Path(parsed.workspace).resolve()
    paths = PathLayout.for_workspace(workspace_root)
    
    workspace_home_id = _detect_workspace_home_id(
        paths.database_path, workspace_root.name
    )
    context = RuntimeContext(
        workspace_home_id=workspace_home_id,
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )
    
    peers_list = [p.strip() for p in parsed.peers.split(",") if p.strip()]
    targets = [(p, None) for p in peers_list]
    resolved_targets = tuple(
        resolve_peer_target(p, profile_id=pid) for p, pid in targets
    )
    adm_cfg = build_broadcast_admission_config(
        resolved_targets,
        clock=context.clock,
        ids=context.ids,
    )
    with create_runtime(context, admission_config=adm_cfg) as runtime:
        coordinator = BroadcastCoordinator(runtime=runtime, clock=context.clock, ids=context.ids)
        caller = require_caller_identity(LocalProcessCallerIdentityProvider())
        req = FanOutRequest(
            workspace_root=workspace_root,
            prompt=parsed.prompt,
            targets=targets,
            required_capability_tier=CapabilityTier[parsed.capability_tier],
            limits=TransportLimits(
                process_timeout_ms=parsed.timeout_seconds * 1000,
                silence_timeout_ms=parsed.silence_timeout_seconds * 1000,
                max_output_bytes=parsed.max_output_bytes,
            ),
            authenticated_subject=caller,
        )
        
        result = coordinator.fan_out(req)
        
        if parsed.json:
            out_obj = {
                "round_id": result.round_id,
                "disposition": result.disposition,
                "legs": [
                    {
                        "target": leg.target,
                        "leg_state": leg.leg_state,
                        "response_text": leg.response_text,
                    }
                    for leg in result.legs
                ]
            }
            print(json.dumps(out_obj, indent=2))
        else:
            print(f"Broadcast Round: {result.round_id} (Disposition: {result.disposition})")
            for leg in result.legs:
                status_icon = "✓" if leg.leg_state == "completed" else "✗"
                print(f"[{status_icon}] {leg.target}: {leg.leg_state}")
                if leg.response_text:
                    print(f"    {leg.response_text.strip()}\n")
                    
        return 0 if result.disposition == "all_completed" else 1


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
    status_group = status_parser.add_mutually_exclusive_group()
    status_group.add_argument("--peer", help="Show quota data for a specific peer")
    status_group.add_argument("--all", action="store_true", help="Show quota data for all peers")

    # Diag subcommand
    diag_parser = subparsers.add_parser("diag", help="Show live peer diagnostics and quota telemetry")
    diag_parser.add_argument("--workspace", default=".", help="Path to workspace root")
    diag_parser.add_argument("--live", action="store_true", help="Run in continuous monitoring loop")
    diag_parser.add_argument("--fresh", action="store_true", help="Bypass telemetry cache")
    diag_parser.add_argument("--no-color", action="store_true", help="Disable terminal colors")
    diag_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    # Broadcast subcommand
    broadcast_parser = subparsers.add_parser("broadcast", help="Broadcast one prompt to multiple peers")
    broadcast_parser.add_argument("prompt", help="Prompt text to broadcast")
    broadcast_parser.add_argument("--peers", default="ag,cx", help="Comma-separated list of peers (default: ag,cx)")
    broadcast_parser.add_argument(
        "--capability-tier",
        default="READ_ONLY",
        choices=tuple(tier.name for tier in CapabilityTier),
        help="Required downstream capability tier",
    )
    broadcast_parser.add_argument("--workspace", default=".", help="Path to workspace root")
    broadcast_parser.add_argument("--timeout-seconds", type=int, default=60)
    broadcast_parser.add_argument("--silence-timeout-seconds", type=int, default=60)
    broadcast_parser.add_argument("--max-output-bytes", type=int, default=1_000_000)
    broadcast_parser.add_argument("--json", action="store_true", help="Emit JSON")

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
        "--capability-tier",
        required=True,
        choices=tuple(tier.name for tier in CapabilityTier),
        help="Required downstream capability tier",
    )
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

    statusline_parser = subparsers.add_parser(
        "statusline",
        help="Format live statusline for an AI peer",
    )
    statusline_parser.add_argument(
        "--peer",
        default="ag",
        choices=["ag", "cc", "cx"],
        help="Target peer identifier (default: ag)",
    )
    statusline_parser.add_argument(
        "--workspace",
        default=".",
        help="Path to workspace root",
    )

    parsed = parser.parse_args(args)

    if parsed.command == "statusline":
        return _run_statusline(parsed)

    if parsed.command == "diag":
        return _run_diag(parsed)

    if parsed.command == "broadcast":
        return _run_broadcast(parsed)

    if parsed.command == "status":
        workspace_root = Path(parsed.workspace).resolve()
        paths = PathLayout.for_workspace(workspace_root)
        
        print(f"Workspace: {workspace_root}")
        print(f"Database: {paths.database_path}")
        
        if not paths.database_path.exists():
            print("Status: Workspace uninitialized (no database found)")
            return 0
            
        workspace_home_id = _detect_workspace_home_id(
            paths.database_path, workspace_root.name
        )
        context = RuntimeContext(
            workspace_home_id=workspace_home_id,
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

            if getattr(parsed, "all", False) or getattr(parsed, "peer", None) is not None:
                with runtime.state_store.read_unit_of_work() as uow:
                    _print_quota_table(uow, parsed.peer)

    if parsed.command == "ask":
        return _run_ask(parsed)
            
    return 0

if __name__ == "__main__":
    sys.exit(main())
