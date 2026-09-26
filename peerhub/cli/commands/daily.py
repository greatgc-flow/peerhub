"""Daily command registrations and behavior-preserving CLI translators."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, cast

from peerhub.application.ingress import InputContractError, resolve_prompt_input
from peerhub.cli.context import resolve_workspace
from peerhub.cli.parser import add_json_arg, add_workspace_arg, help_epilog_kwargs


def register_status_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],  # pyright: ignore[reportPrivateUsage]
) -> None:
    """Register the stable status command at its established order."""

    status_parser = subparsers.add_parser(
        "status",
        help="Show the current workspace status",
        **help_epilog_kwargs(
            "  peerhub status --workspace ./peerhub-demo --all  inspect workspace health and quota data for every peer"
        ),
    )
    status_parser.add_argument(
        "-w", "--workspace", default=None,
        help="Path to the workspace root (default: current directory)",
    )
    status_group = status_parser.add_mutually_exclusive_group()
    status_group.add_argument("--peer", help="Show quota data for a specific peer")
    status_group.add_argument("--all", action="store_true", help="Show quota data for all peers")


def register_daily_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],  # pyright: ignore[reportPrivateUsage]
    *,
    capability_tier_names: tuple[str, ...],
) -> None:
    """Register the stable diagnostic and broadcast command surface."""

    diag_parser = subparsers.add_parser(
        "diag",
        help="Show live peer diagnostics and quota telemetry",
        **help_epilog_kwargs(
            "  peerhub diag --workspace ./peerhub-demo --domains  inspect live telemetry alongside governed work"
        ),
    )
    add_workspace_arg(diag_parser)
    diag_parser.add_argument("--live", action="store_true", help="Run in continuous monitoring loop")
    diag_parser.add_argument("--fresh", action="store_true", help="Bypass telemetry cache")
    diag_parser.add_argument("--no-color", action="store_true", help="Disable terminal colors")
    add_json_arg(diag_parser)
    diag_parser.add_argument(
        "--domains", action="store_true",
        help="Include a governed-domain state section (consensus/task/lesson) alongside peer-CLI telemetry",
    )

    broadcast_parser = subparsers.add_parser(
        "broadcast",
        help="Broadcast one prompt to multiple peers",
        **help_epilog_kwargs(
            "  peerhub broadcast \"List one risk.\" --peers cx,ag --workspace ./peerhub-demo  ask several configured peers"
        ),
    )
    broadcast_parser.add_argument("prompt", nargs="?", default=None, help="Prompt text to broadcast")
    broadcast_parser.add_argument(
        "--query-file", default=None,
        help="Read the prompt from a UTF-8 file instead (exactly one of prompt / --query-file)",
    )
    broadcast_parser.add_argument(
        "--peers", default="ag,cx", help="Comma-separated list of peers (default: ag,cx)"
    )
    broadcast_parser.add_argument(
        "-t", "--capability-tier", default="READ_ONLY", choices=capability_tier_names,
        help="Required downstream capability tier",
    )
    add_workspace_arg(broadcast_parser)
    broadcast_parser.add_argument("--timeout-seconds", type=int, default=60)
    broadcast_parser.add_argument("--silence-timeout-seconds", type=int, default=60)
    broadcast_parser.add_argument("--max-output-bytes", type=int, default=1_000_000)
    add_json_arg(broadcast_parser, help="Emit JSON")

def register_ask_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],  # pyright: ignore[reportPrivateUsage]
    *,
    capability_tier_names: tuple[str, ...],
) -> None:
    """Register ``ask`` at its established position in the root command order."""

    ask_parser = subparsers.add_parser(
        "ask",
        help="Send one prompt to a real peer CLI",
        **help_epilog_kwargs(
            "  peerhub ask cx \"Summarize this repository\" --workspace ./peerhub-demo  send one prompt to a configured peer"
        ),
    )
    ask_parser.add_argument("peer", help="Peer name (ag/agy, cc/claude, cx/codex)")
    ask_parser.add_argument("prompt", nargs="?", default=None, help="Prompt text to send")
    ask_parser.add_argument(
        "--query-file", default=None,
        help="Read the prompt from a UTF-8 file instead (exactly one of prompt / --query-file)",
    )
    ask_parser.add_argument(
        "-t", "--capability-tier", default="READ_ONLY", choices=capability_tier_names,
        help="Required downstream capability tier",
    )
    add_workspace_arg(
        ask_parser,
        help="Path to the workspace root (default: current directory)",
    )
    ask_parser.add_argument("-p", "--profile", default=None, help="Explicit profile ID (a pin: never substituted)")
    ask_parser.add_argument(
        "--effort-hint", default=None,
        help="Declared work shape (a key of routing.preference_map); a declaration, not a measurement",
    )
    ask_parser.add_argument(
        "--effort-routing", default=None, choices=("off", "advisory", "opt-in"),
        help="Override routing.effort_routing for this call (default from policy: advisory)",
    )
    ask_parser.add_argument("--timeout-seconds", type=int, default=60)
    ask_parser.add_argument("--silence-timeout-seconds", type=int, default=60)
    ask_parser.add_argument("--max-output-bytes", type=int, default=1_000_000)
    add_json_arg(ask_parser, help="Emit JSON")


def route_ask_profile(
    parsed: argparse.Namespace, cli: ModuleType, workspace_root: Path, paths: Any
) -> str | None:
    """Apply the declared ``--effort-hint`` (R4 2.8); returns the profile id to dispatch with."""

    hint = getattr(parsed, "effort_hint", None)
    override = getattr(parsed, "effort_routing", None)
    if hint is None and override is None:
        return cast("str | None", parsed.profile)

    from peerhub.adapters.registry import _CLI_ALIASES, resolve_peer_adapter  # pyright: ignore[reportPrivateUsage]
    from peerhub.application.config_paths import (
        resolve_global_config_home,
        resolve_workspace_config_home,
    )
    from peerhub.application.effort_routing import EffortRoutingError, decide_profile
    from peerhub.dispatch.policy_resolver import PolicyResolver
    from peerhub.health.contract import AdmissionState, AvailabilityState

    peer_key = str(parsed.peer).strip()
    if peer_key not in _CLI_ALIASES:
        return cast("str | None", parsed.profile)  # the ask path reports the unsupported peer itself
    peer_kind = _CLI_ALIASES[peer_key]
    resolver = PolicyResolver(
        resolve_workspace_config_home(workspace_root).path / "dispatch-policy.toml",
        resolve_global_config_home().path / "dispatch-policy.toml",
    )
    overrides = {"routing": {"effort_routing": override}} if override is not None else None
    routing = resolver.resolve("ask", cli_overrides=overrides).routing
    registered = {p.profile_id for p in resolve_peer_adapter(peer_kind).descriptor.profiles}

    runtime_cm: Any = None
    health: Any = None
    if paths.database_path.exists():
        try:
            context = cli.RuntimeContext(
                workspace_home_id=cli._detect_workspace_home_id(paths.database_path, workspace_root.name),
                paths=paths, clock=cli.SystemClock(), ids=cli.UuidSource(),
            )
            runtime_cm = cli.create_read_runtime(context, adapter_peer_kind="fake")
            health = runtime_cm.__enter__().health_service
        except Exception:  # health is a best-effort filter; dispatch admission stays authoritative
            runtime_cm, health = None, None

    def is_eligible(binding: str) -> bool:
        if binding not in registered:
            return False
        if health is None:
            return True
        try:
            projection = health.read_health_projection(peer_kind, binding)
        except Exception:
            return True
        if projection is None:
            return True
        return (
            projection.effective_admission_state is AdmissionState.OPEN
            and projection.effective_availability_state is not AvailabilityState.UNAVAILABLE
        )

    try:
        decision = decide_profile(
            peer_kind=peer_kind,
            explicit_profile=parsed.profile,
            hint=hint,
            mode=routing.effort_routing,
            preference_map=routing.preference_map,
            is_eligible=is_eligible,
        )
    except EffortRoutingError as error:
        raise ValueError(str(error)) from error
    finally:
        if runtime_cm is not None:
            runtime_cm.__exit__(None, None, None)
    if decision.note:
        print(f"peerhub ask: {decision.note}", file=cli.sys.stderr)
    return decision.profile_id


def run_ask(
    parsed: argparse.Namespace,
    cli: ModuleType,
    *,
    caller_identity_provider: Any | None = None,
) -> int:
    """Run ``ask`` through injected legacy seams for compatibility."""

    class _AskState:
        supervisor: Any | None = None
        result: Any | None = None
        error: BaseException | None = None

    state = _AskState()
    done = cli.threading.Event()
    thread_started = False
    try:
        authenticated_subject = cli.require_caller_identity(
            caller_identity_provider
            if caller_identity_provider is not None
            else cli.LocalProcessCallerIdentityProvider()
        )
        resolution = resolve_workspace(parsed.workspace)
        workspace_root = resolution.root
        paths = cli.PathLayout.for_workspace(workspace_root)
        guard_code = cli._guard_automatic_workspace_init(
            parsed, resolution, paths
        )
        if guard_code is not None:
            return guard_code
        is_first_init = not paths.database_path.exists()
        prompt_text = resolve_prompt_input(parsed.prompt, parsed.query_file)
        routed_profile = route_ask_profile(parsed, cli, workspace_root, paths)
        request = cli.DirectAskRequest(
            workspace_root=workspace_root,
            peer_name=parsed.peer,
            prompt=prompt_text,
            required_capability_tier=cli.CapabilityTier[parsed.capability_tier],
            profile_id=routed_profile,
            limits=cli.TransportLimits(
                process_timeout_ms=parsed.timeout_seconds * 1000,
                silence_timeout_ms=parsed.silence_timeout_seconds * 1000,
                max_output_bytes=parsed.max_output_bytes,
            ),
        )

        def cancellation_hook(supervisor: Any) -> None:
            state.supervisor = supervisor

        def run_ask_thread() -> None:
            try:
                state.result = cli.execute_direct_ask(
                    request,
                    clock=cli.SystemClock(),
                    ids=cli.UuidSource(),
                    authenticated_subject=authenticated_subject,
                    cancellation_hook=cancellation_hook,
                )
            except BaseException as error:
                state.error = error
            finally:
                done.set()

        thread = cli.threading.Thread(target=run_ask_thread, name="PeerhubDirectAsk")
        thread_started = True
        thread.start()
        while not done.wait(0.1):
            pass
        if state.error is not None:
            raise state.error
        result = state.result
        assert result is not None
        if is_first_init and paths.database_path.exists():
            print(f"[peerhub] initialized workspace at {paths.database_path.parent}", file=cli.sys.stderr)
    except KeyboardInterrupt:
        print("\npeerhub ask: interrupt received; cancelling in-flight process...", file=cli.sys.stderr)
        if thread_started:
            for _ in range(20):
                if state.supervisor is not None:
                    break
                cli.time.sleep(0.05)
            if state.supervisor is not None:
                state.supervisor.begin_cancellation()
            done.wait()
        return 130
    except (
        ValueError,
        cli.ProfileNotFoundError,
        cli.ExecutableNotFoundError,
        cli.ReadinessProbeFailedError,
        cli.HealthPolicyConflictError,
        RuntimeError,
        OSError,
        cli.sqlite3.Error,
    ) as error:
        print(f"peerhub ask: {error}", file=cli.sys.stderr)
        return 2

    exit_code = cli._ask_exit_code(result)
    if parsed.json:
        cli._print_ask_json(result)
    elif exit_code == 0:
        assert result.response_text is not None
        cli.sys.stdout.write(result.response_text)
        if not result.response_text.endswith("\n"):
            cli.sys.stdout.write("\n")
    if exit_code != 0:
        detail = result.error_code or result.request_state or "unknown failure"
        print(f"peerhub ask: {cli._enum_value(detail)}", file=cli.sys.stderr)
    return exit_code


def refresh_usage_projections(
    workspace_root: Any, *, force: bool, freshness_ttl: int, cli: ModuleType
) -> list[Any]:
    """Poll and persist quota projections through the existing runtime seam.

    Never raises: telemetry must not be able to take down `diag`/`status`.
    A provider that fails simply leaves its pool honestly absent. If the
    runtime itself is unavailable (e.g. the workspace store can't be
    opened), the whole poll degrades to an empty projection list rather
    than propagating -- callers that want `--domains` collection to
    still be attempted separately rely on this not aborting `run_diag`.
    """

    from peerhub.telemetry.contract import UsageObserved
    from peerhub.telemetry.quota_polling import (
        poll_agy_usage,
        poll_claude_usage,
        poll_codex_usage,
        record_usage_observations,
    )

    paths = cli.PathLayout.for_workspace(workspace_root)
    if not paths.database_path.exists():
        return []
    try:
        context = cli.RuntimeContext(
            workspace_home_id=cli._detect_workspace_home_id(paths.database_path, workspace_root.name),
            paths=paths,
            clock=cli.SystemClock(),
            ids=cli.UuidSource(),
        )
        with cli.create_read_runtime(context, adapter_peer_kind="fake") as runtime:
            ids = context.ids
            now = int(context.clock.now())
            with runtime.state_store.read_unit_of_work() as uow:
                existing = list(uow.list_usage_projections(None))
            fresh_instances = {
                projection.instance_id
                for projection in existing
                if not force and now - projection.updated_at <= freshness_ttl
            }
            pollers = (("cc", poll_claude_usage), ("cx", poll_codex_usage), ("ag", poll_agy_usage))
            observations: list[UsageObserved] = []
            for instance_id, poll in pollers:
                if instance_id in fresh_instances:
                    continue
                try:
                    # Legacy P:\ / hub.py-environment compatibility: resolves sys_dir
                    # via PEERHUB_SYS_DIR if set, falling back to legacy workspace_root / "_sys".
                    daily_sys_dir = (
                        Path(os.environ["PEERHUB_SYS_DIR"])
                        if os.environ.get("PEERHUB_SYS_DIR")
                        else (workspace_root / "_sys")
                    )
                    observations.extend(poll(ids, instance_id, "standard", freshness_ttl=freshness_ttl, sys_dir=daily_sys_dir))
                except Exception as poll_error:
                    # Surface the failure rather than silently presenting it as
                    # "no fresh telemetry" -- a rate-limited or network-failed
                    # poll is a different condition than "nothing to report"
                    # and callers (diag/status) should not mask that distinction.
                    print(
                        f"peerhub: usage poll for {instance_id!r} failed: {poll_error}",
                        file=cli.sys.stderr,
                    )
                    continue
            if observations:
                with runtime.state_store.unit_of_work() as uow:
                    record_usage_observations(uow, ids, observations)
                    uow.commit()
            with runtime.state_store.read_unit_of_work() as uow:
                return list(uow.list_usage_projections(None))
    except Exception as error:
        print(f"Error: failed to refresh usage projections: {error}", file=cli.sys.stderr)
        return []


def render_domain_section(domains: Mapping[str, Any]) -> str:
    if domains.get("status") == "unavailable":
        return str(domains.get("reason", "governance state unavailable"))
    lines: list[str] = []
    for name in ("consensus", "tasks", "lessons"):
        rows = cast(list[Mapping[str, Any]], domains.get(name, []))
        lines.append(name.upper() + ": " + str(len(rows)))
        for row in rows:
            lines.append("  " + str(row.get("summary", row.get("target_id", "unknown"))))
    lines.append("DUTY LEASES: unavailable (cross-room enumeration not implemented)")
    return "\n".join(lines)


def run_diag(parsed: argparse.Namespace, cli: ModuleType) -> int:
    from peerhub.telemetry.presenter import TelemetryPresenter

    workspace_root = resolve_workspace(parsed.workspace).root
    projections = cli._refresh_usage_projections(
        workspace_root, force=bool(getattr(parsed, "fresh", False))
    )
    presenter = TelemetryPresenter(
        use_color=False if parsed.no_color else None,
        workspace_root=workspace_root,
        usage_projections=projections,
    )

    def with_domains(snapshot: dict[str, Any]) -> dict[str, Any]:
        if not getattr(parsed, "domains", False):
            return snapshot
        try:
            from peerhub.governance.activity import (
                list_active_consensus_rounds,
                list_active_lessons,
                list_active_tasks,
            )
            paths = cli.PathLayout.for_workspace(workspace_root)
            if not paths.database_path.exists():
                snapshot["domains"] = {
                    "consensus": [],
                    "tasks": [],
                    "lessons": [],
                    "duty_leases": {
                        "status": "unavailable",
                        "reason": (
                            "cross-room duty lease enumeration is not implemented"
                        ),
                    },
                }
                return snapshot
            context = cli.RuntimeContext(
                workspace_home_id=cli._detect_workspace_home_id(paths.database_path, workspace_root.name),
                paths=paths,
                clock=cli.SystemClock(),
                ids=cli.UuidSource(),
            )
            with cli.create_read_runtime(context, adapter_peer_kind="fake") as runtime:
                now = int(cli.time.time())
                consensus = list_active_consensus_rounds(runtime.governance_broker)
                tasks = list_active_tasks(runtime.governance_broker)
                lessons = list_active_lessons(runtime.governance_broker)
                domain_data = {
                    "consensus": [{"target_id": target.target_id, "revision": target.revision, "state": dict(target.state), "summary": cli.format_consensus_row(dict(target.state), now)} for target in consensus],
                    "tasks": [{"target_id": target.target_id, "revision": target.revision, "state": dict(target.state), "summary": cli.format_task_row(dict(target.state))} for target in tasks],
                    "lessons": [{"target_id": target.target_id, "revision": target.revision, "state": dict(target.state)} for target in lessons],
                    "duty_leases": {"status": "unavailable", "reason": "cross-room duty lease enumeration is not implemented"},
                }
                snapshot["domains"] = cli._json_safe(domain_data)
        except Exception as error:
            snapshot["domains"] = {"status": "unavailable", "reason": f"governance state unavailable: {error}"}
        return snapshot

    if parsed.live:
        try:
            import msvcrt
        except ImportError:
            msvcrt = None
        try:
            while True:
                cli.sys.stdout.write("\033[2J\033[H")
                cli.sys.stdout.flush()
                snapshot = with_domains(presenter.collect_live_snapshot())
                if parsed.json:
                    print(cli.json.dumps(snapshot, indent=2))
                else:
                    rendered = presenter.render(snapshot)
                    if getattr(parsed, "domains", False):
                        rendered += "\n\nGOVERNED DOMAINS\n" + cli._render_domain_section(snapshot["domains"])
                    print(rendered)
                    print(presenter.format_ansi(" [Live Monitor Active: Press ESC or 'q' to exit]", "dim"))
                elapsed = 0.0
                while elapsed < 2.0:
                    # msvcrt is Windows-only; typeshed's stub only exposes
                    # kbhit/getch when pyright's pythonPlatform is Windows,
                    # which this project's pinned "Linux" (matching CI)
                    # never is -- correctly guarded at runtime by the
                    # try/except ImportError above, not a real type error.
                    if msvcrt is not None and msvcrt.kbhit():  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
                        if msvcrt.getch() in (b"\x1b", b"q", b"Q", b"\x03"):  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
                            return 0
                    cli.time.sleep(0.05)
                    elapsed += 0.05
        except KeyboardInterrupt:
            return 0
    snapshot = with_domains(presenter.collect_live_snapshot())
    if parsed.json:
        print(cli.json.dumps(snapshot, indent=2))
    else:
        rendered = presenter.render(snapshot)
        if getattr(parsed, "domains", False):
            rendered += "\n\nGOVERNED DOMAINS\n" + cli._render_domain_section(snapshot["domains"])
        print(rendered)
    return 0


def run_status(parsed: argparse.Namespace, cli: ModuleType) -> int:
    workspace_root = resolve_workspace(parsed.workspace).root
    paths = cli.PathLayout.for_workspace(workspace_root)
    print(f"Workspace: {workspace_root}")
    print(f"Database: {paths.database_path}")
    if not paths.database_path.exists():
        print("Status: Workspace uninitialized (no database found)")
        return 0
    context = cli.RuntimeContext(
        workspace_home_id=cli._detect_workspace_home_id(paths.database_path, workspace_root.name),
        paths=paths,
        clock=cli.SystemClock(),
        ids=cli.UuidSource(),
    )
    try:
        with cli.create_read_runtime(context, adapter_peer_kind="fake") as runtime:
            conn = runtime.state_store._connect_read()  # pyright: ignore[reportPrivateUsage]
            try:
                migrations = runtime.state_store._migration_versions(conn)  # pyright: ignore[reportPrivateUsage]
                print(f"Schema Migrations Applied: {len(migrations)}")
            finally:
                conn.close()
            print("Health Circuit ('system'): (no listing API exists yet -- not queryable from the CLI)")
            print(f"Active Leases: {runtime.dispatch_service.count_active_leases()}")
            print("Status: OK")
            if getattr(parsed, "all", False) or getattr(parsed, "peer", None) is not None:
                cli._refresh_usage_projections(workspace_root, force=False)
                with runtime.state_store.read_unit_of_work() as uow:
                    cli._print_quota_table(uow, parsed.peer)
    except (RuntimeError, cli.sqlite3.Error) as error:
        print(f"peerhub status: {error}", file=cli.sys.stderr)
        return 2
    return 0


def run_broadcast(parsed: argparse.Namespace, cli: ModuleType) -> int:
    from peerhub.application.broadcast import BroadcastCoordinator, FanOutRequest

    try:
        prompt_text = resolve_prompt_input(parsed.prompt, parsed.query_file)
    except InputContractError as error:
        print(f"peerhub broadcast: {error}", file=cli.sys.stderr)
        return 2

    resolution = resolve_workspace(parsed.workspace)
    workspace_root = resolution.root
    paths = cli.PathLayout.for_workspace(workspace_root)
    guard_code = cli._guard_automatic_workspace_init(parsed, resolution, paths)
    if guard_code is not None:
        return guard_code
    is_first_init = not paths.database_path.exists()
    context = cli.RuntimeContext(
        workspace_home_id=cli._detect_workspace_home_id(paths.database_path, workspace_root.name),
        paths=paths,
        clock=cli.SystemClock(),
        ids=cli.UuidSource(),
    )
    targets: list[tuple[str, str | None]] = [
        (str(peer.strip()), None)
        for peer in parsed.peers.split(",")
        if peer.strip()
    ]
    resolved_targets = tuple(cli.resolve_peer_target(peer, profile_id=profile) for peer, profile in targets)
    admission_config = cli.build_broadcast_admission_config(
        resolved_targets, clock=context.clock, ids=context.ids
    )
    with cli.create_runtime(context, admission_config=admission_config) as runtime:
        coordinator = BroadcastCoordinator(runtime=runtime, clock=context.clock, ids=context.ids)
        request = FanOutRequest(
            workspace_root=workspace_root,
            prompt=prompt_text,
            targets=targets,
            required_capability_tier=cli.CapabilityTier[parsed.capability_tier],
            limits=cli.TransportLimits(
                process_timeout_ms=parsed.timeout_seconds * 1000,
                silence_timeout_ms=parsed.silence_timeout_seconds * 1000,
                max_output_bytes=parsed.max_output_bytes,
            ),
            authenticated_subject=cli.require_caller_identity(cli.LocalProcessCallerIdentityProvider()),
        )
        result = coordinator.fan_out(request)
        if is_first_init and paths.database_path.exists():
            print(
                f"[peerhub] initialized workspace at {paths.database_path.parent}",
                file=cli.sys.stderr,
            )
        if parsed.json:
            print(cli.json.dumps({"round_id": result.round_id, "disposition": result.disposition, "legs": [{"target": leg.target, "leg_state": leg.leg_state, "response_text": leg.response_text} for leg in result.legs]}, indent=2))
        else:
            print(f"Broadcast Round: {result.round_id} (Disposition: {result.disposition})")
            for leg in result.legs:
                status_icon = "✓" if leg.leg_state == "completed" else "✗"
                print(f"[{status_icon}] {leg.target}: {leg.leg_state}")
                if leg.response_text:
                    print(f"    {leg.response_text.strip()}\n")
        return 0 if result.disposition == "all_completed" else 1
