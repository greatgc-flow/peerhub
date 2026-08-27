"""Command-line interface for PeerHub."""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from peerhub.persistence.sqlite import SqliteReadUnitOfWork
    from peerhub.telemetry.contract import UsageProjectionSnapshot

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
from peerhub.application.lesson_broadcast import LessonBroadcastCoordinator
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
from peerhub.governance.consensus import ConsensusService
from peerhub.governance.tasks import TaskService
from peerhub.governance.lessons import LessonService
from peerhub.governance.rooms import HANDOFF_LIST_SECTIONS, RoomsService
from peerhub.governance.activity import rebuild_room_session_bindings
from peerhub.dispatch.duty_lease import (
    DutyLeaseSnapshot,
    DutyOwnerIdentity,
)
from peerhub.dispatch.room_session import (
    RoomParticipationCoordinator,
    RoomSessionEndRequest,
    RoomSessionHeartbeatRequest,
    RoomSessionOpenRequest,
    RoomSessionSnapshot,
)
from peerhub.dispatch.terminal_duty import TerminalDutyService
from peerhub.core.errors import InvalidMutationError, RecordNotFoundError
from peerhub.telemetry.domain_rows import format_consensus_row, format_task_row

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

def _refresh_usage_projections(
    workspace_root: Path,
    *,
    force: bool,
    freshness_ttl: int = 60,
) -> list["UsageProjectionSnapshot"]:
    """Poll the real quota sources, persist them, and return current projections.

    This is the seam that was missing: `quota_polling`'s pollers and
    `record_usage_observations()` were fully built and tested but had no
    caller, so CC/CX quota was always empty in a real run.

    `force` (i.e. `--fresh`) polls unconditionally. Otherwise a projection
    that is still inside `freshness_ttl` is reused as-is and no provider is
    contacted; only stale or absent data triggers a poll.

    Never raises: telemetry must not be able to take down `diag`/`status`.
    A provider that fails simply leaves its pool honestly absent.
    """
    from peerhub.core.context import PathLayout, RuntimeContext
    from peerhub.runtime import create_runtime
    from peerhub.telemetry.quota_polling import (
        poll_agy_usage,
        poll_claude_usage,
        poll_codex_usage,
        record_usage_observations,
    )

    paths = PathLayout.for_workspace(workspace_root)
    try:
        context = RuntimeContext(
            workspace_home_id=_detect_workspace_home_id(
                paths.database_path, workspace_root.name
            ),
            paths=paths,
            clock=SystemClock(),
            ids=UuidSource(),
        )
        with create_runtime(context, adapter_peer_kind="fake") as runtime:
            ids = context.ids
            now = int(context.clock.now())

            with runtime.state_store.read_unit_of_work() as uow:
                existing = list(uow.list_usage_projections(None))

            fresh_instances = set()
            if not force:
                for proj in existing:
                    if now - proj.updated_at <= freshness_ttl:
                        fresh_instances.add(proj.instance_id)

            pollers = (
                ("cc", poll_claude_usage),
                ("cx", poll_codex_usage),
                ("ag", poll_agy_usage),
            )
            observations = []
            poll_sys_dir = workspace_root / "_sys"
            for instance_id, poll in pollers:
                if instance_id in fresh_instances:
                    continue
                try:
                    observations.extend(
                        poll(
                            ids,
                            instance_id,
                            "standard",
                            freshness_ttl=freshness_ttl,
                            sys_dir=poll_sys_dir,
                        )
                    )
                except Exception:
                    # Fail closed for this peer only; absent beats fabricated.
                    continue

            if observations:
                with runtime.state_store.unit_of_work() as uow:
                    record_usage_observations(uow, ids, observations)
                    # SqliteUnitOfWork rolls back on exit unless committed
                    # explicitly; without this the projections are written
                    # inside the transaction and then discarded.
                    uow.commit()

            with runtime.state_store.read_unit_of_work() as uow:
                return list(uow.list_usage_projections(None))
    except Exception:
        return []


def _run_diag(parsed: argparse.Namespace) -> int:
    from peerhub.telemetry.presenter import TelemetryPresenter
    workspace_root = Path(parsed.workspace).resolve()
    projections = _refresh_usage_projections(
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
            paths = PathLayout.for_workspace(workspace_root)

            context = RuntimeContext(
                workspace_home_id=_detect_workspace_home_id(
                    paths.database_path, workspace_root.name
                ),
                paths=paths,
                clock=SystemClock(),
                ids=UuidSource(),
            )
            with create_runtime(context, adapter_peer_kind="fake") as runtime:
                now = int(time.time())
                consensus = list_active_consensus_rounds(runtime.governance_broker)
                tasks = list_active_tasks(runtime.governance_broker)
                lessons = list_active_lessons(runtime.governance_broker)
                domain_data = {
                    "consensus": [{"target_id": t.target_id, "revision": t.revision, "state": dict(t.state), "summary": format_consensus_row(dict(t.state), now)} for t in consensus],
                    "tasks": [{"target_id": t.target_id, "revision": t.revision, "state": dict(t.state), "summary": format_task_row(dict(t.state))} for t in tasks],
                    "lessons": [{"target_id": t.target_id, "revision": t.revision, "state": dict(t.state)} for t in lessons],
                    "duty_leases": {"status": "unavailable", "reason": "cross-room duty lease enumeration is not implemented"},
                }
                snapshot["domains"] = _json_safe(domain_data)
        except Exception as exc:
            snapshot["domains"] = {"status": "unavailable", "reason": f"governance state unavailable: {exc}"}
        return snapshot
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

                snapshot = with_domains(presenter.collect_live_snapshot())
                if parsed.json:
                    print(json.dumps(snapshot, indent=2))
                else:
                    rendered = presenter.render(snapshot)
                    if getattr(parsed, "domains", False):
                        rendered += "\n\nGOVERNED DOMAINS\n" + _render_domain_section(snapshot["domains"])
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
        snapshot = with_domains(presenter.collect_live_snapshot())
        if parsed.json:
            print(json.dumps(snapshot, indent=2))
        else:
            rendered = presenter.render(snapshot)
            if getattr(parsed, "domains", False):
                rendered += "\n\nGOVERNED DOMAINS\n" + _render_domain_section(snapshot["domains"])
            print(rendered)
        return 0


def _render_domain_section(domains: Mapping[str, Any]) -> str:
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

    # Save to status log under peerhub's own workspace-relative state dir
    # (not a hardcoded Engram "_sys" layout -- see
    # engram_peerhub_separation_proposal.md row 3.6).
    workspace_root = Path(parsed.workspace).resolve()
    paths = PathLayout.for_workspace(workspace_root)
    log_dest = paths.workspace_home / "statusline" / "ag_statusline_stdin.log"
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
        print("ag:Gemini | ctx:ok | hub:idle", end="")
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


def _json_safe(value: Any) -> Any:
    """Recursively convert frozen TargetState.state values (Mapping/tuple,
    from core.protocol.freeze_json_mapping) into plain dict/list so
    json.dumps doesn't choke on a nested mappingproxy -- dict(x) alone only
    converts the top level, not values nested inside it."""
    if isinstance(value, Mapping):
        items = cast("Mapping[Any, Any]", value).items()
        return {key: _json_safe(item) for key, item in items}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in cast("list[Any] | tuple[Any, ...]", value)]
    return value


def _run_consensus(parsed: argparse.Namespace) -> int:
    workspace_root = Path(parsed.workspace).resolve()
    paths = PathLayout.for_workspace(workspace_root)
    # No pre-check on paths.database_path.exists() here: create_runtime()
    # below already calls state_store.initialize(), which creates the
    # database on first use. `propose` legitimately needs to be able to
    # initialize a fresh workspace; `vote`/`status` on a nonexistent round
    # in a fresh (or existing) database correctly fall through to the real
    # RecordNotFoundError path below, a more accurate error than a blanket
    # "workspace uninitialized" would be either way.
    context = RuntimeContext(
        workspace_home_id=_detect_workspace_home_id(paths.database_path, workspace_root.name),
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )
    try:
        with create_runtime(context, adapter_peer_kind="fake") as runtime:
            service = ConsensusService(runtime.governance_broker, clock=context.clock, ids=context.ids)
            if parsed.consensus_action == "propose":
                required = tuple(item for item in parsed.required.split(",") if item)
                eligible = tuple(item for item in parsed.eligible.split(",") if item)
                submission = service.propose(
                    round_id=parsed.round_id,
                    title=parsed.title,
                    question=parsed.question,
                    body=parsed.body,
                    proposer_id=parsed.proposer,
                    required_participants=required,
                    eligible_participants=eligible,
                    risk=parsed.risk,
                    source_hash="sha256:" + hashlib.sha256(parsed.body.encode()).hexdigest(),
                )
                target = runtime.governance_broker.get_target(submission.receipt.target_id)
                assert target is not None
                state = cast(dict[str, Any], target.state)
                quorum = cast(dict[str, Any], state["quorum"])
                payload: dict[str, Any] = {"round_id": parsed.round_id, "phase": state["phase"], "quorum_required": quorum["required_votes"]}
                if parsed.json:
                    print(json.dumps(_json_safe(payload)))
                else:
                    print(f"Consensus round {parsed.round_id} proposed (phase={payload['phase']}, quorum required={payload['quorum_required']})")
                return 0
            if parsed.consensus_action == "vote":
                submission = service.cast_vote(parsed.round_id, actor_id=parsed.actor, choice=parsed.choice)
                target = runtime.governance_broker.get_target(submission.receipt.target_id)
                assert target is not None
                state = cast(dict[str, Any], target.state)
                payload: dict[str, Any] = {"round_id": parsed.round_id, "phase": state["phase"], "quorum": state["quorum"]}
                if parsed.json:
                    print(json.dumps(_json_safe(payload)))
                else:
                    quorum = payload["quorum"]
                    print(f"Consensus vote recorded for {parsed.round_id} (phase={payload['phase']}, votes={quorum['counted_votes']}/{quorum['required_votes']}, quorum reached={quorum['reached']})")
                return 0
            if parsed.consensus_action == "list":
                targets = runtime.governance_broker.list_targets(
                    "consensus-round", None
                )
                proposals = [
                    {
                        "target_id": target.target_id,
                        "revision": target.revision,
                        "state": target.state,
                    }
                    for target in targets
                ]
                if parsed.json:
                    print(json.dumps(_json_safe({"proposals": proposals})))
                elif not proposals:
                    print("No consensus proposals found.")
                else:
                    print("Consensus proposals:")
                    for proposal in proposals:
                        state = cast(Mapping[str, Any], proposal["state"])
                        votes = cast(Mapping[str, Any], state.get("votes", {}))
                        participants = cast(
                            Mapping[str, Any], state.get("participants", {})
                        )
                        required = cast(
                            tuple[Any, ...] | list[Any],
                            participants.get("required", ()),
                        )
                        print(
                            f"{proposal['target_id']}: phase={state.get('phase', 'unknown')}, "
                            f"status={state.get('status', 'unknown')}, "
                            f"votes={len(votes)}/{len(required)}"
                        )
                return 0
            target = runtime.governance_broker.get_target(parsed.round_id)
            if target is None:
                raise RecordNotFoundError("consensus-round", parsed.round_id)
            if parsed.json:
                print(json.dumps(_json_safe(target.state)))
            else:
                state = cast(dict[str, Any], target.state)
                quorum = cast(dict[str, Any], state["quorum"])
                print(f"Consensus round {parsed.round_id}: phase={state['phase']}, votes={quorum['counted_votes']}/{quorum['required_votes']}, quorum reached={quorum['reached']}")
            return 0
    except (InvalidMutationError, RecordNotFoundError, ValueError) as exc:
        print(f"peerhub consensus: {exc}", file=sys.stderr)
        return 2


def _run_task(parsed: argparse.Namespace) -> int:
    workspace_root = Path(parsed.workspace).resolve()
    paths = PathLayout.for_workspace(workspace_root)
    context = RuntimeContext(
        workspace_home_id=_detect_workspace_home_id(paths.database_path, workspace_root.name),
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )
    try:
        with create_runtime(context, adapter_peer_kind="fake") as runtime:
            service = TaskService(runtime.governance_broker, clock=context.clock, ids=context.ids)
            action = parsed.task_action
            if action == "create":
                submission = service.create(task_id=parsed.task_id, summary=parsed.summary, spec=parsed.spec, creator_id=parsed.creator, room_id=parsed.room_id or None)
            elif action == "claim-start":
                submission = service.claim_start(parsed.task_id, actor_id=parsed.actor, request_id=parsed.request_id, coordinator=parsed.coordinator, attempt_id=parsed.attempt_id)
            elif action == "checkpoint":
                submission = service.checkpoint(parsed.task_id, actor_id=parsed.actor, checkpoint_id=parsed.checkpoint_id, stage=parsed.stage, request_id=parsed.request_id, attempt_id=parsed.attempt_id, resume_token_ref=parsed.resume_token or None, completed_units=tuple(x for x in parsed.completed.split(",") if x), remaining_units=tuple(x for x in parsed.remaining.split(",") if x))
            elif action == "complete":
                submission = service.complete(parsed.task_id, actor_id=parsed.actor)
            elif action == "fail":
                submission = service.fail(parsed.task_id, actor_id=parsed.actor, failure_class=parsed.failure_class, reason=parsed.reason)
            elif action == "cancel":
                submission = service.cancel(parsed.task_id, actor_id=parsed.actor, reason=parsed.reason)
            else:
                target = runtime.governance_broker.get_target(parsed.task_id)
                if target is None:
                    raise RecordNotFoundError("task", parsed.task_id)
                if parsed.json:
                    print(json.dumps(_json_safe(target.state)))
                else:
                    print(f"Task {parsed.task_id}: state={target.state['state']}")
                return 0
            target = runtime.governance_broker.get_target(submission.receipt.target_id)
            assert target is not None
            state = cast(dict[str, Any], target.state)
            payload = _json_safe(target.state)
            if parsed.json:
                print(json.dumps(payload))
            else:
                verb = {"create": "created", "claim-start": "started", "checkpoint": "checkpointed", "complete": "completed", "fail": "failed", "cancel": "cancelled"}[action]
                print(f"Task {parsed.task_id} {verb} (state={state['state']})")
            return 0
    except (InvalidMutationError, RecordNotFoundError, ValueError) as exc:
        print(f"peerhub task: {exc}", file=sys.stderr)
        return 2


def _run_lesson(parsed: argparse.Namespace) -> int:
    workspace_root = Path(parsed.workspace).resolve()
    paths = PathLayout.for_workspace(workspace_root)
    context = RuntimeContext(workspace_home_id=_detect_workspace_home_id(paths.database_path, workspace_root.name), paths=paths, clock=SystemClock(), ids=UuidSource())
    try:
        with create_runtime(context, adapter_peer_kind="fake") as runtime:
            service = LessonService(runtime.governance_broker, clock=context.clock, ids=context.ids)
            action = parsed.lesson_action
            if action == "propose":
                submission = service.propose(lesson_id=parsed.lesson_id, title=parsed.title, rule=parsed.rule, category=parsed.category, severity=parsed.severity, proposer_id=parsed.proposer, affected_peers=tuple(x for x in parsed.affected.split(",") if x), scope_kind=parsed.scope_kind, workspace_id=parsed.workspace_id)
            elif action == "approve":
                submission = service.approve(parsed.lesson_id, approved_by_actor_id=parsed.approved_by, authority_target_id=parsed.authority_target_id)
            elif action == "activate":
                submission = service.activate(parsed.lesson_id, actor_id=parsed.actor)
            elif action == "retire":
                submission = service.retire(parsed.lesson_id, actor_id=parsed.actor, reason=parsed.reason)
            elif action == "supersede":
                submission = service.supersede(parsed.lesson_id, actor_id=parsed.actor, replacement_lesson_id=parsed.replacement_lesson_id)
            elif action == "quarantine":
                submission = service.quarantine(parsed.lesson_id, actor_id=parsed.actor, reason=parsed.reason, evidence=parsed.evidence)
            elif action == "broadcast":
                result = LessonBroadcastCoordinator(
                    broker=runtime.governance_broker,
                    lessons=service,
                    rooms=runtime.rooms_service,
                ).broadcast(
                    lesson_id=parsed.lesson_id,
                    room_id=parsed.room_id,
                    sender_instance_id=parsed.sender_instance_id,
                    sender_profile_id=parsed.sender_profile_id,
                    created_at=context.clock.now(),
                )
                payload = {
                    "campaign_id": result.campaign_id,
                    "campaign_target_id": result.campaign_target_id,
                    "lesson_id": result.lesson_id,
                    "room_id": result.room_id,
                    "recipient_profile_ids": result.recipient_profile_ids,
                    "inbox_message_target_ids": result.inbox_message_target_ids,
                    "delivery_target_ids": result.delivery_target_ids,
                }
                if parsed.json:
                    print(json.dumps(_json_safe(payload)))
                elif result.recipient_profile_ids:
                    print(
                        f"LESSON-BROADCAST {parsed.lesson_id} -> "
                        f"{','.join(result.recipient_profile_ids)}"
                    )
                else:
                    print(
                        f"LESSON-BROADCAST {parsed.lesson_id} | no targets "
                        "(no other room members)"
                    )
                return 0
            else:
                target = runtime.governance_broker.get_target(f"lesson:{parsed.lesson_id}")
                if target is None:
                    raise RecordNotFoundError("lesson", parsed.lesson_id)
                if parsed.json:
                    print(json.dumps(_json_safe(target.state)))
                else:
                    print(f"Lesson {parsed.lesson_id}: lifecycle={target.state['lifecycle']}")
                return 0
            target = runtime.governance_broker.get_target(submission.receipt.target_id)
            assert target is not None
            state = cast(dict[str, Any], target.state)
            if parsed.json:
                print(json.dumps(_json_safe(target.state)))
            else:
                verb = {"propose": "proposed", "approve": "approved", "activate": "activated", "retire": "retired", "supersede": "superseded", "quarantine": "quarantined"}[action]
                print(f"Lesson {parsed.lesson_id} {verb} (lifecycle={state['lifecycle']})")
            return 0
    except (InvalidMutationError, RecordNotFoundError, ValueError) as exc:
        print(f"peerhub lesson: {exc}", file=sys.stderr)
        return 2


def _run_room(parsed: argparse.Namespace) -> int:
    workspace_root = Path(parsed.workspace).resolve()
    paths = PathLayout.for_workspace(workspace_root)
    context = RuntimeContext(workspace_home_id=_detect_workspace_home_id(paths.database_path, workspace_root.name), paths=paths, clock=SystemClock(), ids=UuidSource())
    try:
        with create_runtime(context, adapter_peer_kind="fake") as runtime:
            service = RoomsService(runtime.governance_broker, clock=context.clock, ids=context.ids)
            action = parsed.room_action
            if action == "create":
                submission = service.create_room(room_id=parsed.room_id, topic_id=parsed.topic_id, title=parsed.title, creator_id=parsed.creator, participants=tuple(x for x in parsed.participants.split(",") if x))
            elif action == "create-thread":
                submission = service.create_thread(thread_id=parsed.thread_id, room_id=parsed.room_id, subject=parsed.subject, creator_id=parsed.creator)
            elif action == "append-message":
                submission = service.append_message(message_id=parsed.message_id, room_id=parsed.room_id, thread_id=parsed.thread_id, author_id=parsed.author, body=parsed.body)
            elif action == "send":
                submission = service.send_message(
                    room_id=parsed.room_id,
                    sender_instance_id=parsed.sender_instance_id,
                    sender_profile_id=parsed.sender_profile_id,
                    recipient_instance_id=parsed.recipient_instance_id,
                    recipient_profile_id=parsed.recipient_profile_id,
                    body=parsed.body,
                    message_type=parsed.message_type,
                    thread_ref=parsed.thread_ref,
                    resource_ref=parsed.resource_ref,
                    correlation_id=parsed.correlation_id,
                )
            elif action == "check-inbox":
                messages = service.check_inbox(
                    room_id=parsed.room_id,
                    caller_instance_id=parsed.caller_instance_id,
                    caller_profile_id=parsed.caller_profile_id,
                    include_read=parsed.include_read,
                )
                result = {
                    "messages": [
                        {
                            "target_id": message.target_id,
                            "revision": message.revision,
                            "state": message.state,
                        }
                        for message in messages
                    ]
                }
                if parsed.json:
                    print(json.dumps(_json_safe(result)))
                else:
                    print(
                        f"Inbox for {parsed.caller_instance_id}/"
                        f"{parsed.caller_profile_id}: {len(messages)} message(s)"
                    )
                    for message in messages:
                        print(
                            f"- [{message.state['sequence']}] "
                            f"{message.state['message_type']}: "
                            f"{message.state['body']}"
                        )
                return 0
            elif action == "mark-read":
                submission = service.mark_read(
                    room_id=parsed.room_id,
                    recipient_instance_id=parsed.recipient_instance_id,
                    recipient_profile_id=parsed.recipient_profile_id,
                    up_through_sequence=parsed.up_through_sequence,
                )
            elif action == "promote-message":
                submission = service.promote_message(
                    message_id=parsed.message_id,
                    room_id=parsed.room_id,
                    thread_id=parsed.thread_id,
                    actor_id=parsed.actor,
                )
            elif action == "react":
                submission = service.react(message_id=parsed.message_id, room_id=parsed.room_id, actor_instance_id=parsed.actor_instance_id, actor_profile_id=parsed.actor_profile_id, reaction_type=parsed.reaction_type)
            elif action == "unreact":
                submission = service.unreact(message_id=parsed.message_id, room_id=parsed.room_id, actor_instance_id=parsed.actor_instance_id, actor_profile_id=parsed.actor_profile_id, reaction_type=parsed.reaction_type)
            elif action == "append-handoff":
                submission = service.append_handoff_note(
                    room_id=parsed.room_id,
                    section=parsed.section,
                    text=parsed.text,
                    actor_id=parsed.actor,
                )
            elif action == "checkpoint":
                checkpoint = service.checkpoint(
                    parsed.room_id,
                    actor_id=parsed.actor or "peerhub",
                )
                export_format = parsed.export or (
                    "json" if parsed.json else "markdown"
                )
                if export_format == "json":
                    print(json.dumps(_json_safe(checkpoint)))
                else:
                    print(checkpoint["markdown"])
                return 0
            elif action == "context-fill":
                selected_sections = (
                    None
                    if parsed.sections is None
                    else tuple(
                        section.strip()
                        for section in parsed.sections.split(",")
                        if section.strip()
                    )
                )
                context_envelope = service.context_fill(
                    parsed.room_id,
                    session_id=parsed.session_id,
                    sections=selected_sections,
                )
                print(json.dumps(_json_safe(context_envelope)))
                return 0
            elif action == "clear":
                submission = service.clear_room(parsed.room_id, new_room_id=parsed.new_room_id, subject=parsed.subject, actor_id=parsed.actor)
            elif action == "rebuild-session-bindings":
                submission = rebuild_room_session_bindings(
                    runtime.governance_broker,
                    parsed.room_id,
                    runtime.room_participation_coordinator.list_active_sessions(
                        parsed.room_id
                    ),
                )
            else:
                target = runtime.governance_broker.get_target(parsed.room_id)
                if target is None:
                    raise RecordNotFoundError("room", parsed.room_id)
                if parsed.json:
                    print(json.dumps(_json_safe(target.state)))
                else:
                    print(f"Room {parsed.room_id}: status={target.state['status']}")
                return 0
            target = runtime.governance_broker.get_target(submission.receipt.target_id)
            assert target is not None
            state = cast(dict[str, Any], target.state)
            if parsed.json:
                print(json.dumps(_json_safe(target.state)))
            elif action == "create-thread":
                print(f"Thread {parsed.thread_id} created in room {parsed.room_id}")
            elif action == "append-message":
                print(f"Message {parsed.message_id} appended to thread {parsed.thread_id} (sequence={state['sequence']})")
            elif action == "send":
                print(
                    f"Mailbox message delivered to "
                    f"{parsed.recipient_instance_id}/{parsed.recipient_profile_id} "
                    f"(sequence={state['sequence']})"
                )
            elif action == "mark-read":
                print(
                    f"Inbox marked read through sequence "
                    f"{state['read_through_sequence']}"
                )
            elif action == "promote-message":
                print(
                    f"Mailbox message {parsed.message_id} promoted to "
                    f"thread {parsed.thread_id}"
                )
            elif action == "react":
                print(f"Reaction {parsed.reaction_type} added to message {parsed.message_id}")
            elif action == "unreact":
                print(f"Reaction {parsed.reaction_type} removed from message {parsed.message_id}")
            elif action == "append-handoff":
                print(
                    f"Handoff note appended to {parsed.section} "
                    f"for room {parsed.room_id}"
                )
            elif action == "clear":
                print(f"Room {parsed.room_id} cleared -> new room {parsed.new_room_id}")
            elif action == "rebuild-session-bindings":
                print(f"Room {parsed.room_id} session bindings rebuilt")
            else:
                print(f"Room {parsed.room_id} created")
            return 0
    except (InvalidMutationError, RecordNotFoundError, ValueError) as exc:
        print(f"peerhub room: {exc}", file=sys.stderr)
        return 2


def _run_duty(parsed: argparse.Namespace) -> int:
    workspace_root = Path(parsed.workspace).resolve()
    paths = PathLayout.for_workspace(workspace_root)
    context = RuntimeContext(workspace_home_id=_detect_workspace_home_id(paths.database_path, workspace_root.name), paths=paths, clock=SystemClock(), ids=UuidSource())
    try:
        with create_runtime(context, adapter_peer_kind="fake") as runtime:
            coordinator = runtime.duty_lease_coordinator
            service = TerminalDutyService(coordinator, default_heartbeat_timeout_ms=parsed.heartbeat_timeout_ms if hasattr(parsed, "heartbeat_timeout_ms") else 60_000)
            owner = cast(DutyOwnerIdentity, DutyOwnerIdentity(parsed.instance_id, parsed.profile_id) if hasattr(parsed, "instance_id") else None)
            if parsed.duty_action == "claim":
                lease = service.claim_terminal_duty(parsed.room_id, owner, parsed.owner_principal_id, parsed.authority_epoch)
            elif parsed.duty_action == "heartbeat":
                lease = service.send_heartbeat(parsed.lease_id, parsed.room_id, owner, parsed.term, parsed.authority_epoch)
            elif parsed.duty_action == "close":
                if parsed.close_session and (
                    not parsed.session_id
                    or parsed.session_generation is None
                    or parsed.session_generation < 1
                    or not parsed.workspace_scope_id
                    or not parsed.actor_principal_id
                ):
                    raise ValueError(
                        "--close-session requires --session-id, "
                        "--session-generation, --workspace-scope-id, "
                        "and --actor-principal-id"
                    )
                lease = service.close_terminal_duty(parsed.lease_id, parsed.room_id, owner, parsed.term, parsed.authority_epoch)
                if parsed.close_session:
                    duty_close: dict[str, Any] = {
                        "status": "ok",
                        "lease": _duty_lease_payload(lease),
                    }
                    try:
                        session = runtime.room_participation_coordinator.end_session(
                            RoomSessionEndRequest(
                                session_id=parsed.session_id,
                                session_generation=parsed.session_generation,
                                workspace_scope_id=parsed.workspace_scope_id,
                                room_id=parsed.room_id,
                                actor_principal_id=parsed.actor_principal_id,
                                owner=owner,
                            )
                        )
                    except (
                        InvalidMutationError,
                        RecordNotFoundError,
                        ValueError,
                    ) as exc:
                        result = {
                            "duty_close": duty_close,
                            "session_close": {
                                "status": "failed",
                                "reason": f"{type(exc).__name__}: {exc}",
                            },
                        }
                        if parsed.json:
                            print(json.dumps(result))
                        else:
                            print(
                                f"Terminal duty lease {lease.lease_id} closed"
                            )
                            print(
                                f"Room session close failed: {exc}",
                                file=sys.stderr,
                            )
                        return 2
                    result = {
                        "duty_close": duty_close,
                        "session_close": {
                            "status": "ok",
                            "session_id": session.session_id,
                            "session_generation": (
                                session.session_generation
                            ),
                            "state": session.state.value,
                        },
                    }
                    if parsed.json:
                        print(json.dumps(result))
                    else:
                        print(
                            f"Terminal duty lease {lease.lease_id} closed"
                        )
                        print(f"Room session {session.session_id} ended")
                    return 0
            elif parsed.duty_action == "sweep":
                leases = coordinator.sweep_expired_leases(
                    parsed.role,
                    recovery_actor_principal_id=(
                        parsed.recovery_actor_principal_id
                    ),
                    trigger=parsed.trigger,
                    evidence_digest=parsed.evidence_digest,
                    policy_id=parsed.policy_id,
                    policy_revision=parsed.policy_revision,
                )
                result = {
                    "expired_count": len(leases),
                    "leases": [
                        _duty_lease_payload(item) for item in leases
                    ],
                }
                if parsed.json:
                    print(json.dumps(result))
                else:
                    print(
                        f"Expired {len(leases)} {parsed.role} duty "
                        "lease(s)"
                    )
                return 0
            else:
                holder = service.active_terminal_holder(parsed.room_id)
                if parsed.json:
                    print(json.dumps(_json_safe({"room_id": parsed.room_id, "owner": None if holder is None else {"instance_id": holder.instance_id, "profile_id": holder.profile_id}})))
                else:
                    print(f"Terminal duty for room {parsed.room_id}: UNHELD" if holder is None else f"Terminal duty for room {parsed.room_id}: held by {holder.instance_id}/{holder.profile_id}")
                return 0
            payload = _duty_lease_payload(lease)
            if parsed.json:
                print(json.dumps(payload))
            elif parsed.duty_action == "claim":
                print(f"Terminal duty claimed for room {lease.room_id} (lease={lease.lease_id}, epoch={lease.authority_epoch})")
            elif parsed.duty_action == "heartbeat":
                print(f"Heartbeat sent for lease {lease.lease_id}")
            else:
                print(f"Terminal duty lease {lease.lease_id} closed")
            return 0
    except (InvalidMutationError, RecordNotFoundError, ValueError) as exc:
        print(f"peerhub duty: {exc}", file=sys.stderr)
        return 2


def _duty_lease_payload(lease: DutyLeaseSnapshot) -> dict[str, Any]:
    return {
        "lease_id": lease.lease_id,
        "room_id": lease.room_id,
        "role": lease.role,
        "owner": {
            "instance_id": lease.owner.instance_id,
            "profile_id": lease.owner.profile_id,
        },
        "owner_principal_id": lease.owner_principal_id,
        "authority_epoch": lease.authority_epoch,
        "term": lease.term,
        "state": lease.state.value,
        "heartbeat_expires_at": lease.heartbeat_expires_at,
    }


def _room_session_payload(session: RoomSessionSnapshot) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "workspace_scope_id": session.workspace_scope_id,
        "room_id": session.room_id,
        "actor_principal_id": session.actor_principal_id,
        "owner": {
            "instance_id": session.owner.instance_id,
            "profile_id": session.owner.profile_id,
        },
        "session_fingerprint": session.session_fingerprint,
        "session_generation": session.session_generation,
        "resume_parent_session_id": session.resume_parent_session_id,
        "state": session.state.value,
        "heartbeat_expires_at": session.heartbeat_expires_at,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


def _run_session(parsed: argparse.Namespace) -> int:
    workspace_root = Path(parsed.workspace).resolve()
    paths = PathLayout.for_workspace(workspace_root)
    context = RuntimeContext(
        workspace_home_id=_detect_workspace_home_id(
            paths.database_path, workspace_root.name
        ),
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )
    try:
        with create_runtime(context, adapter_peer_kind="fake") as runtime:
            coordinator = RoomParticipationCoordinator(
                runtime.state_store,
                clock=context.clock,
                ids=context.ids,
            )
            owner = DutyOwnerIdentity(parsed.instance_id, parsed.profile_id)
            if parsed.session_action == "open":
                session = coordinator.open_session(
                    RoomSessionOpenRequest(
                        workspace_scope_id=parsed.workspace_scope_id,
                        room_id=parsed.room_id,
                        actor_principal_id=parsed.actor_principal_id,
                        owner=owner,
                        session_fingerprint=parsed.session_fingerprint,
                        heartbeat_timeout_ms=parsed.heartbeat_timeout_ms,
                    )
                )
            else:
                if parsed.session_action == "heartbeat":
                    session = coordinator.heartbeat(
                        RoomSessionHeartbeatRequest(
                            session_id=parsed.session_id,
                            session_generation=parsed.session_generation,
                            workspace_scope_id=parsed.workspace_scope_id,
                            room_id=parsed.room_id,
                            actor_principal_id=parsed.actor_principal_id,
                            owner=owner,
                        ),
                        heartbeat_timeout_ms=parsed.heartbeat_timeout_ms,
                    )
                else:
                    session = coordinator.end_session(
                        RoomSessionEndRequest(
                            session_id=parsed.session_id,
                            session_generation=parsed.session_generation,
                            workspace_scope_id=parsed.workspace_scope_id,
                            room_id=parsed.room_id,
                            actor_principal_id=parsed.actor_principal_id,
                            owner=owner,
                        )
                    )

            if parsed.json:
                print(json.dumps(_room_session_payload(session)))
            elif parsed.session_action == "open":
                print(
                    f"Room session opened for {session.room_id} "
                    f"(session={session.session_id}, "
                    f"generation={session.session_generation})"
                )
            elif parsed.session_action == "heartbeat":
                print(f"Heartbeat sent for room session {session.session_id}")
            else:
                print(f"Room session {session.session_id} closed")
            return 0
    except (InvalidMutationError, RecordNotFoundError, ValueError) as exc:
        print(f"peerhub session: {exc}", file=sys.stderr)
        return 2


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
    diag_parser.add_argument("--domains", action="store_true", help="Include a governed-domain state section (consensus/task/lesson) alongside peer-CLI telemetry")

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

    consensus_parser = subparsers.add_parser("consensus", help="Manage consensus rounds")
    consensus_subparsers = consensus_parser.add_subparsers(dest="consensus_action", required=True)
    propose_parser = consensus_subparsers.add_parser("propose", help="Propose a new consensus round")
    propose_parser.add_argument("--workspace", default=".", help="Path to the workspace root")
    propose_parser.add_argument("--round-id", required=True, help="Consensus round identifier")
    propose_parser.add_argument("--title", required=True, help="Short proposal title")
    propose_parser.add_argument("--question", required=True, help="Question for participants")
    propose_parser.add_argument("--body", required=True, help="Proposal details")
    propose_parser.add_argument("--proposer", required=True, help="Proposer peer ID")
    propose_parser.add_argument("--required", required=True, help="Comma-separated peer IDs required for quorum (for example: cc,cx,ag)")
    propose_parser.add_argument("--eligible", required=True, help="Comma-separated eligible peer IDs")
    propose_parser.add_argument("--risk", default="normal", help="Risk tier used for quorum calculation (default: normal)")
    propose_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    list_parser = consensus_subparsers.add_parser(
        "list",
        help="List every consensus proposal, including resolved rounds",
    )
    list_parser.add_argument(
        "--workspace",
        default=".",
        help="Path to the workspace root containing consensus state",
    )
    list_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON",
    )
    for action in ("vote", "status"):
        command_parser = consensus_subparsers.add_parser(action, help="Cast a vote" if action == "vote" else "Read a consensus round")
        command_parser.add_argument("--workspace", default=".", help="Path to the workspace root")
        command_parser.add_argument("--round-id", required=True, help="Consensus round identifier")
        command_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
        if action == "vote":
            command_parser.add_argument("--actor", required=True, help="Voting peer ID")
            command_parser.add_argument("--choice", required=True, choices=("agree", "disagree"), help="Vote choice")

    task_parser = subparsers.add_parser("task", help="Manage task lifecycles")
    task_subparsers = task_parser.add_subparsers(dest="task_action", required=True)
    task_specs = {
        "create": ([("--task-id", True), ("--summary", True), ("--spec", True), ("--creator", True), ("--room-id", False)],),
        "claim-start": ([("--task-id", True), ("--actor", True), ("--request-id", True), ("--coordinator", True), ("--attempt-id", True)],),
        "checkpoint": ([("--task-id", True), ("--actor", True), ("--checkpoint-id", True), ("--stage", True), ("--request-id", True), ("--attempt-id", True), ("--resume-token", False), ("--completed", False), ("--remaining", False)],),
        "complete": ([("--task-id", True), ("--actor", True)],),
        "fail": ([("--task-id", True), ("--actor", True), ("--failure-class", True), ("--reason", True)],),
        "cancel": ([("--task-id", True), ("--actor", True), ("--reason", True)],),
        "status": ([("--task-id", True)],),
    }
    task_subcommand_help = {
        "create": "Create a new task",
        "claim-start": "Bind an executor and start running a task",
        "checkpoint": "Record a resumable checkpoint for a running task",
        "complete": "Mark a running task as succeeded",
        "fail": "Mark a task as failed, recording a failure class and reason",
        "cancel": "Cancel a task before it reaches a terminal state",
        "status": "Show the current state of a task",
    }
    task_arg_help = {
        "--task-id": "Task identifier",
        "--summary": "Short one-line summary of the task's objective",
        "--spec": "Full task specification / instructions",
        "--creator": "Peer ID creating the task",
        "--room-id": "Room this task belongs to (omit for no room scope)",
        "--actor": "Peer ID performing this action",
        "--request-id": "Dispatch request ID bound to this task",
        "--coordinator": "Peer ID coordinating this task's execution",
        "--attempt-id": "Dispatch attempt ID bound to this task",
        "--checkpoint-id": "Identifier for this checkpoint",
        "--stage": "Name of the task stage this checkpoint captures",
        "--resume-token": "Opaque token an executor can use to resume from this checkpoint",
        "--completed": "Comma-separated completed unit IDs (e.g. unit-1,unit-2)",
        "--remaining": "Comma-separated remaining unit IDs (e.g. unit-3,unit-4)",
        "--failure-class": "Category of failure (e.g. timeout, validation_error)",
        "--reason": "Human-readable reason for this action",
    }
    for action, (arguments,) in task_specs.items():
        command_parser = task_subparsers.add_parser(action, help=task_subcommand_help[action])
        command_parser.add_argument("--workspace", default=".", help="Path to the workspace root")
        for name, required in arguments:
            command_parser.add_argument(name, required=required, default="", help=task_arg_help[name])
        command_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    lesson_parser = subparsers.add_parser("lesson", help="Manage governance lessons")
    lesson_subparsers = lesson_parser.add_subparsers(dest="lesson_action", required=True)
    lesson_specs = {
        "propose": [("--lesson-id", True), ("--title", True), ("--rule", True), ("--category", True), ("--severity", True), ("--proposer", True), ("--affected", True), ("--scope-kind", False), ("--workspace-id", False)],
        "approve": [("--lesson-id", True), ("--approved-by", True), ("--authority-target-id", False)],
        "activate": [("--lesson-id", True), ("--actor", True)],
        "retire": [("--lesson-id", True), ("--actor", True), ("--reason", False)],
        "supersede": [("--lesson-id", True), ("--actor", True), ("--replacement-lesson-id", True)],
        "quarantine": [("--lesson-id", True), ("--actor", True), ("--reason", True), ("--evidence", True)],
        "broadcast": [("--lesson-id", True), ("--room-id", True), ("--sender-instance-id", True), ("--sender-profile-id", True)],
        "status": [("--lesson-id", True)],
    }
    lesson_subcommand_help = {
        "propose": "Propose a new governance lesson",
        "approve": "Approve a proposed lesson, authorizing later activation",
        "activate": "Activate an approved lesson (requires prior approval)",
        "retire": "Retire an active lesson",
        "supersede": "Mark an active lesson as superseded by a replacement lesson",
        "quarantine": "Quarantine a lesson due to a correctness/evidence concern",
        "broadcast": "Immediately deliver an active lesson to every other room participant",
        "status": "Show the current state of a lesson",
    }
    lesson_arg_help = {
        "--lesson-id": "Lesson identifier",
        "--title": "Short one-line lesson title",
        "--rule": "The rule or guidance this lesson establishes",
        "--category": "Lesson category (e.g. runtime-reality, process)",
        "--severity": "Severity level (e.g. LOW, MEDIUM, HIGH)",
        "--proposer": "Peer ID proposing this lesson",
        "--affected": "Comma-separated affected peer IDs (e.g. cc,cx,ag)",
        "--scope-kind": "Scope kind for this lesson (default: global)",
        "--workspace-id": "Workspace ID this lesson applies to, if scope-kind is not global",
        "--approved-by": "Actor ID approving this lesson",
        "--authority-target-id": "Reference to the consensus round or authority record backing this approval",
        "--actor": "Peer ID performing this action",
        "--reason": "Human-readable reason for this action (default for retire: MANUAL)",
        "--replacement-lesson-id": "Lesson ID that supersedes this one",
        "--evidence": "Evidence supporting the quarantine decision",
        "--room-id": "Room whose participants should receive this lesson",
        "--sender-instance-id": "Sending participant's terminal instance identifier",
        "--sender-profile-id": "Sending participant's profile identifier",
    }
    for action, arguments in lesson_specs.items():
        command_parser = lesson_subparsers.add_parser(action, help=lesson_subcommand_help[action])
        command_parser.add_argument("--workspace", default=".", help="Path to the workspace root")
        for name, required in arguments:
            if name in {"--workspace-id", "--authority-target-id"}:
                default = None
            elif name == "--reason" and action == "retire":
                default = "MANUAL"
            elif name == "--scope-kind":
                default = "global"
            else:
                default = ""
            command_parser.add_argument(name, required=required, default=default, help=lesson_arg_help[name])
        command_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    room_parser = subparsers.add_parser("room", help="Manage rooms and messages")
    room_subparsers = room_parser.add_subparsers(dest="room_action", required=True)
    room_specs = {
        "create": [("--room-id", True), ("--topic-id", True), ("--title", True), ("--creator", True), ("--participants", True)],
        "create-thread": [("--thread-id", True), ("--room-id", True), ("--subject", True), ("--creator", True)],
        "append-message": [("--message-id", True), ("--room-id", True), ("--thread-id", True), ("--author", True), ("--body", True)],
        "send": [("--room-id", True), ("--sender-instance-id", True), ("--sender-profile-id", True), ("--recipient-instance-id", True), ("--recipient-profile-id", True), ("--body", True), ("--message-type", False), ("--thread-ref", False), ("--resource-ref", False), ("--correlation-id", False)],
        "check-inbox": [("--room-id", True), ("--caller-instance-id", True), ("--caller-profile-id", True), ("--include-read", False)],
        "mark-read": [("--room-id", True), ("--recipient-instance-id", True), ("--recipient-profile-id", True), ("--up-through-sequence", True)],
        "promote-message": [("--message-id", True), ("--room-id", True), ("--thread-id", True), ("--actor", True)],
        "react": [("--message-id", True), ("--room-id", True), ("--actor-instance-id", True), ("--actor-profile-id", True), ("--reaction-type", True)],
        "unreact": [("--message-id", True), ("--room-id", True), ("--actor-instance-id", True), ("--actor-profile-id", True), ("--reaction-type", True)],
        "append-handoff": [("--room-id", True), ("--section", True), ("--text", True), ("--actor", True)],
        "checkpoint": [("--room-id", True), ("--actor", False)],
        "context-fill": [("--room-id", True), ("--session-id", True), ("--sections", False)],
        "clear": [("--room-id", True), ("--new-room-id", True), ("--subject", True), ("--actor", True)],
        "rebuild-session-bindings": [("--room-id", True)],
        "status": [("--room-id", True)],
    }
    room_subcommand_help = {
        "create": "Create a new room",
        "create-thread": "Create a new thread inside an existing room",
        "append-message": "Append a message to a thread",
        "send": "Deliver one private mailbox message to a room recipient",
        "check-inbox": "Read this caller's private mailbox without marking messages read",
        "mark-read": "Advance one recipient's mailbox read cursor through a delivery sequence",
        "promote-message": "Copy one mailbox message into a thread and record the promotion",
        "react": "Record this peer's active reaction to a message",
        "unreact": "Remove this peer's active reaction from a message",
        "append-handoff": "Append an immutable note to the room's continuity history",
        "checkpoint": "Generate and record the room's bounded handoff projection",
        "context-fill": "Read bounded room continuity for an LLM context window",
        "clear": "Start a fresh room boundary; the old room is preserved untouched",
        "rebuild-session-bindings": "Rebuild the room's session-binding projection from active sessions",
        "status": "Show the current state of a room",
    }
    room_arg_help = {
        "--room-id": "Room identifier",
        "--topic-id": "Topic identifier for this room",
        "--title": "Room title",
        "--creator": "Peer ID creating this room/thread",
        "--participants": "Comma-separated participant peer IDs (e.g. cc,cx,ag)",
        "--thread-id": "Thread identifier",
        "--subject": "Thread subject, or the new room's subject when clearing",
        "--message-id": "Message identifier",
        "--author": "Peer ID authoring this message",
        "--body": "Message body text",
        "--sender-instance-id": "Sending peer's terminal instance identifier",
        "--sender-profile-id": "Sending peer's profile identifier",
        "--recipient-instance-id": "Recipient terminal instance identifier",
        "--recipient-profile-id": "Recipient profile identifier",
        "--caller-instance-id": "Checking peer's terminal instance identifier",
        "--caller-profile-id": "Checking peer's profile identifier",
        "--message-type": "Mailbox message type (default: MSG)",
        "--thread-ref": "Optional related thread identifier",
        "--resource-ref": "Optional opaque related resource reference",
        "--correlation-id": "Optional shared correlation ID for related deliveries",
        "--include-read": "Include messages at or below the current read cursor",
        "--up-through-sequence": "Delivery sequence through which to mark this inbox read",
        "--section": "Handoff section receiving the note",
        "--text": "Continuity note text to append",
        "--session-id": "Session identifier echoed in the context envelope",
        "--sections": "Comma-separated exact section names; omit to return all six",
        "--actor-instance-id": "Reacting peer's terminal instance identifier",
        "--actor-profile-id": "Reacting peer's profile identifier",
        "--reaction-type": "Reaction label or emoji to add or remove (for example ACK or 👍)",
        "--new-room-id": "Identifier for the fresh room created by clear",
        "--actor": "Peer ID performing this action",
    }
    for action, arguments in room_specs.items():
        command_parser = room_subparsers.add_parser(action, help=room_subcommand_help[action])
        command_parser.add_argument("--workspace", default=".", help="Path to the workspace root")
        for name, required in arguments:
            if action == "append-handoff" and name == "--section":
                command_parser.add_argument(
                    name,
                    required=required,
                    choices=HANDOFF_LIST_SECTIONS,
                    help=room_arg_help[name],
                )
            elif action == "check-inbox" and name == "--include-read":
                command_parser.add_argument(
                    name,
                    action="store_true",
                    help=room_arg_help[name],
                )
            elif action == "mark-read" and name == "--up-through-sequence":
                command_parser.add_argument(
                    name,
                    required=required,
                    type=int,
                    help=room_arg_help[name],
                )
            elif action == "send" and name == "--message-type":
                command_parser.add_argument(
                    name,
                    required=required,
                    default="MSG",
                    help=room_arg_help[name],
                )
            else:
                command_parser.add_argument(name, required=required, help=room_arg_help[name])
        if action == "checkpoint":
            command_parser.add_argument(
                "--export",
                choices=("markdown", "json"),
                default=None,
                help=(
                    "Export format; defaults to Markdown, or JSON when "
                    "--json is supplied"
                ),
            )
        command_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    duty_parser = subparsers.add_parser("duty", help="Manage terminal duty")
    duty_subparsers = duty_parser.add_subparsers(dest="duty_action", required=True)
    duty_specs = {
        "claim": [("--room-id", True), ("--instance-id", True), ("--profile-id", True), ("--owner-principal-id", True), ("--authority-epoch", True)],
        "heartbeat": [("--lease-id", True), ("--room-id", True), ("--instance-id", True), ("--profile-id", True), ("--term", True), ("--authority-epoch", True)],
        "close": [("--lease-id", True), ("--room-id", True), ("--instance-id", True), ("--profile-id", True), ("--term", True), ("--authority-epoch", True)],
        "sweep": [("--role", False), ("--recovery-actor-principal-id", True), ("--trigger", False), ("--evidence-digest", True), ("--policy-id", True), ("--policy-revision", True)],
        "status": [("--room-id", True)],
    }
    duty_subcommand_help = {
        "claim": "Claim terminal duty for a room",
        "heartbeat": "Renew (heartbeat) an active terminal-duty lease",
        "close": "Voluntarily release an active terminal-duty lease",
        "sweep": "Expire and record recovery for timed-out duty leases",
        "status": "Show who currently holds terminal duty for a room",
    }
    duty_arg_help = {
        "--room-id": "Room this duty lease is scoped to",
        "--lease-id": "Duty lease identifier (from a prior claim)",
        "--instance-id": "Owner's instance ID (part of the composite peer identity)",
        "--profile-id": "Owner's profile ID (part of the composite peer identity)",
        "--owner-principal-id": "Principal ID that authorized this owner to hold duty",
        "--authority-epoch": "Fencing token; must strictly increase on each new claim",
        "--term": "Opaque leadership-generation token from the current lease (not a timestamp)",
        "--role": "Duty role to sweep across all rooms (default: terminal-duty)",
        "--recovery-actor-principal-id": "Principal recording recovery of each expired lease",
        "--trigger": "Recovery trigger recorded on each receipt (default: HEARTBEAT_TIMEOUT)",
        "--evidence-digest": "Evidence digest supporting this expiry sweep",
        "--policy-id": "Recovery policy identifier governing this sweep",
        "--policy-revision": "Recovery policy revision governing this sweep",
        "--close-session": "Also end the independently fenced room-participation session",
        "--session-id": "Room-participation session identifier to end",
        "--session-generation": "Monotonic generation of the room-participation session",
        "--workspace-scope-id": "Workspace scope owning the room-participation session",
        "--actor-principal-id": "Actor principal owning the room-participation session",
    }
    for action, arguments in duty_specs.items():
        command_parser = duty_subparsers.add_parser(action, help=duty_subcommand_help[action])
        command_parser.add_argument("--workspace", default=".", help="Path to the workspace root")
        for name, required in arguments:
            default = (
                "terminal-duty" if name == "--role"
                else "HEARTBEAT_TIMEOUT" if name == "--trigger"
                else None
            )
            command_parser.add_argument(
                name,
                required=required,
                default=default,
                type=int if name in {"--term", "--authority-epoch"} else str,
                help=duty_arg_help[name],
            )
        if action == "claim":
            command_parser.add_argument("--heartbeat-timeout-ms", type=int, default=60_000, help="Heartbeat timeout in milliseconds (default: 60000)")
        if action == "close":
            command_parser.add_argument(
                "--close-session",
                action="store_true",
                help=duty_arg_help["--close-session"],
            )
            command_parser.add_argument(
                "--session-id",
                default="",
                help=duty_arg_help["--session-id"],
            )
            command_parser.add_argument(
                "--session-generation",
                type=int,
                default=None,
                help=duty_arg_help["--session-generation"],
            )
            command_parser.add_argument(
                "--workspace-scope-id",
                default="",
                help=duty_arg_help["--workspace-scope-id"],
            )
            command_parser.add_argument(
                "--actor-principal-id",
                default="",
                help=duty_arg_help["--actor-principal-id"],
            )
        command_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    session_parser = subparsers.add_parser(
        "session", help="Manage room-participation sessions"
    )
    session_subparsers = session_parser.add_subparsers(
        dest="session_action", required=True
    )
    session_specs = {
        "open": [
            ("--workspace-scope-id", True),
            ("--room-id", True),
            ("--actor-principal-id", True),
            ("--instance-id", True),
            ("--profile-id", True),
            ("--session-fingerprint", True),
        ],
        "heartbeat": [
            ("--session-id", True),
            ("--session-generation", True),
            ("--workspace-scope-id", True),
            ("--room-id", True),
            ("--actor-principal-id", True),
            ("--instance-id", True),
            ("--profile-id", True),
        ],
        "close": [
            ("--session-id", True),
            ("--session-generation", True),
            ("--workspace-scope-id", True),
            ("--room-id", True),
            ("--actor-principal-id", True),
            ("--instance-id", True),
            ("--profile-id", True),
        ],
    }
    session_subcommand_help = {
        "open": "Open or resume this peer's participation in a room",
        "heartbeat": "Renew an active room-participation session",
        "close": "End an active room-participation session",
    }
    session_arg_help = {
        "--session-id": "Room-session identifier returned by session open",
        "--session-generation": "Current session-generation fencing token",
        "--workspace-scope-id": "Workspace scope that owns the room",
        "--room-id": "Room in which this peer is participating",
        "--actor-principal-id": "Principal represented by this session",
        "--instance-id": "Participant instance ID used in the session fence",
        "--profile-id": "Participant profile ID used in the session fence",
        "--session-fingerprint": "Stable fingerprint used to detect a resumable or replaced session",
    }
    for action, arguments in session_specs.items():
        command_parser = session_subparsers.add_parser(
            action, help=session_subcommand_help[action]
        )
        command_parser.add_argument(
            "--workspace",
            default=".",
            help="Path to the workspace root containing PeerHub state",
        )
        for name, required in arguments:
            command_parser.add_argument(
                name,
                required=required,
                type=int if name == "--session-generation" else str,
                help=session_arg_help[name],
            )
        if action in {"open", "heartbeat"}:
            command_parser.add_argument(
                "--heartbeat-timeout-ms",
                type=int,
                default=60_000,
                help=(
                    "Milliseconds of liveness granted by this operation "
                    "(default: 60000)"
                ),
            )
        command_parser.add_argument(
            "--json",
            action="store_true",
            help="Emit the complete persisted session snapshot as JSON",
        )

    parsed = parser.parse_args(args)

    if parsed.command == "statusline":
        return _run_statusline(parsed)

    if parsed.command == "consensus":
        return _run_consensus(parsed)

    if parsed.command == "task":
        return _run_task(parsed)

    if parsed.command == "lesson":
        return _run_lesson(parsed)

    if parsed.command == "room":
        return _run_room(parsed)

    if parsed.command == "duty":
        return _run_duty(parsed)

    if parsed.command == "session":
        return _run_session(parsed)

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
                # Poll-on-demand: without this the projections table is only
                # ever read, never written, so the quota table is permanently
                # empty in a real deployment.
                _refresh_usage_projections(workspace_root, force=False)
                with runtime.state_store.read_unit_of_work() as uow:
                    _print_quota_table(uow, parsed.peer)

    if parsed.command == "ask":
        return _run_ask(parsed)
            
    return 0

if __name__ == "__main__":
    sys.exit(main())
