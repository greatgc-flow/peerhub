"""Command-line interface for PeerHub."""

import argparse
import hashlib
import json
import os  # pyright: ignore[reportUnusedImport] -- command-module compatibility seam
import shutil  # pyright: ignore[reportUnusedImport] -- command-module compatibility seam
import sqlite3  # pyright: ignore[reportUnusedImport] -- command-module compatibility seam
import subprocess  # pyright: ignore[reportUnusedImport] -- command-module compatibility seam
import sys
import threading  # pyright: ignore[reportUnusedImport] -- command-module compatibility seam
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from peerhub.cli.context import WorkspaceResolution, resolve_workspace
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from peerhub.persistence.sqlite import SqliteReadUnitOfWork
    from peerhub.telemetry.contract import UsageProjectionSnapshot
    from peerhub.runtime import Runtime

from peerhub.adapters.registry import (
    ExecutableNotFoundError,  # pyright: ignore[reportUnusedImport] -- command-module compatibility seam
    ProfileNotFoundError,  # pyright: ignore[reportUnusedImport] -- command-module compatibility seam
    resolve_peer_target,  # pyright: ignore[reportUnusedImport] -- command-module compatibility seam
)
from peerhub.application.bootstrap import (
    HealthPolicyConflictError,  # pyright: ignore[reportUnusedImport] -- command-module compatibility seam
    ReadinessProbeFailedError,  # pyright: ignore[reportUnusedImport] -- command-module compatibility seam
    build_broadcast_admission_config,  # pyright: ignore[reportUnusedImport] -- command-module compatibility seam
)
from peerhub.application.direct_ask import (
    DirectAskRequest,  # pyright: ignore[reportUnusedImport] -- command-module compatibility seam
    DirectAskResult,
    execute_direct_ask,  # pyright: ignore[reportUnusedImport] -- command-module compatibility seam
)
from peerhub.application.peer_registry import collect_model_status
from peerhub.application.proposals import load_proposal_voters  # pyright: ignore[reportUnusedImport] -- command-module compatibility seam
from peerhub.application.role_assignment import RoleReleaseDisposition
from peerhub.application.status import collect_room_status
from peerhub.application.broker_status import collect_effect_status
from peerhub.application.legacy import legacy_thread_slug
from peerhub.application.config_paths import resolve_config_paths  # pyright: ignore[reportUnusedImport] -- command-module compatibility seam
from peerhub.application.arbiter_review import load_final_arbiter_policy  # pyright: ignore[reportUnusedImport] -- command-module compatibility seam
from peerhub.application.workspace_identity import detect_workspace_home_id
from peerhub.core.context import Clock, IdSource, PathLayout, RuntimeContext
from peerhub.core.execution import ExecutionCertainty, TransportLimits  # pyright: ignore[reportUnusedImport] -- command-module compatibility seam
from peerhub.core.protocol import CommandOutcome, JsonValue
from peerhub.core.identity import (
    CallerIdentityProvider,
    LocalProcessCallerIdentityProvider,
    require_caller_identity,
)
from peerhub.dispatch.contract import RequestState
from peerhub.dispatch.capability import CapabilityTier
from peerhub.dispatch.process import ProcessSupervisor  # pyright: ignore[reportUnusedImport] -- command-module compatibility seam
from peerhub.runtime import create_read_runtime, create_runtime
from peerhub.client import Client
from peerhub.application.commands import Command, SubmissionMetadata
from peerhub.application.commands.operational_errors import ReportErrorCommand
from peerhub.application.commands.feedback import (
    FeedbackAddCommand,
    FeedbackResolveCommand,
)
from peerhub.application.commands.artifacts import (
    ArtifactClaimCommand,
    ArtifactFinalizeCommand,
    ArtifactStatusCommand,
)
from peerhub.application.commands.locks import (
    LockAcquireCommand,
    LockReleaseCommand,
)
from peerhub.application.commands.tasks import (
    TaskCancelCommand,
    TaskClaimStartCommand,
    TaskCheckpointCommand,
    TaskCompleteCommand,
    TaskCreateCommand,
    TaskFailCommand,
)
from peerhub.application.commands.consensus import (
    ArbiterReviewCommand,
    ConsensusProposeCommand,
    ConsensusVoteCommand,
    ProposalAddCommand,
    ProposalVoteCommand,
)
from peerhub.application.commands.leadership import (
    LeaderClaimCommand,
    LeaderYieldCommand,
)
from peerhub.application.commands.roles import (
    AssignRoleCommand,
    ReleaseRoleCommand,
)
from peerhub.application.commands.lessons import (
    LessonActivateCommand,
    LessonApproveCommand,
    LessonBroadcastCommand,
    LessonQuarantineCommand,
    LessonRetireCommand,
    LessonSupersedeCommand,
    LessonSweepCommand,
)
from peerhub.application.commands.rooms import (
    AppendHandoffCommand,
    ClearRoomCommand,
    ContinuityCheckpointCommand,
    CreateRoomCommand,
    MessageMarkReadCommand,
    MessageSendCommand,
    NewTopicCommand,
    RoomBroadcastCommand,
    RebuildRoomSessionBindingsCommand,
    ThreadAppendCommand,
    ThreadNewCommand,
    ThreadPromoteCommand,
    ThreadReactCommand,
    UpdateStatusCommand,
)
from peerhub.core.ports import RequestContext
from peerhub.governance.lessons import LessonService
from peerhub.governance.rooms import HANDOFF_LIST_SECTIONS, RoomsService
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
from peerhub.core.errors import InvalidMutationError, RecordNotFoundError, PeerHubError
from peerhub.telemetry.domain_rows import format_consensus_row, format_task_row  # pyright: ignore[reportUnusedImport] -- command-module compatibility seam
from peerhub.cli.parser import create_root_parser

class SystemClock(Clock):
    """Real system clock for production use."""
    def now(self) -> int:
        return int(time.time())

class UuidSource(IdSource):
    """Real UUID source for production use."""
    def new_id(self, namespace: str) -> str:
        del namespace
        return str(uuid.uuid4())


def _ask_exit_code(result: DirectAskResult) -> int:  # pyright: ignore[reportUnusedFunction] -- command-module compatibility seam
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


def _print_ask_json(result: DirectAskResult) -> None:  # pyright: ignore[reportUnusedFunction] -- command-module compatibility seam
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


def _run_health(parsed: argparse.Namespace) -> int:
    from peerhub.application.health_revalidation import (
        collect_health_check,
        collect_health_precheck,
        collect_health_sweep,
    )
    from peerhub.adapters.registry import resolve_peer_target
    from peerhub.application.bootstrap import build_direct_ask_admission_config

    workspace_root = resolve_workspace(parsed.workspace).root
    paths = PathLayout.for_workspace(workspace_root)
    read_only = (
        parsed.health_action in ("precheck", "sweep")
        or (parsed.health_action == "check" and not parsed.recover)
    )
    guard_code = _guard_implicit_workspace_init(
        parsed, paths, creating=not read_only
    )
    if guard_code == 0:
        print("Workspace uninitialized; no health state to report.")
    if guard_code is not None:
        return guard_code
    context = RuntimeContext(
        workspace_home_id=_detect_workspace_home_id(paths.database_path, workspace_root.name),
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )
    try:
        if parsed.health_action == "revalidate":
            target = resolve_peer_target(parsed.peer)
            admission_config = build_direct_ask_admission_config(target, clock=context.clock, ids=context.ids)
            with create_runtime(context, adapter_peer_kind=parsed.peer, admission_config=admission_config) as runtime:
                coordinator = runtime.health_revalidation_coordinator
                caller = require_caller_identity(LocalProcessCallerIdentityProvider())
                
                result = coordinator.request_revalidation(
                    peer_node_id=parsed.peer,
                    caller=caller,
                    reason=parsed.reason,
                    requested_at=context.clock.now()
                )
                if parsed.json:
                    print(json.dumps({
                        "probe_outcome": result.probe_outcome.value,
                        "admission_state": result.admission_state.value,
                        "availability_state": result.availability_state.value,
                        "circuit_closed": result.circuit_closed
                    }))
                else:
                    print(f"Revalidation outcome for {parsed.peer}: probe={result.probe_outcome.value}, "
                          f"admission={result.admission_state.value}, "
                          f"availability={result.availability_state.value}, "
                          f"circuit_closed={result.circuit_closed}")
                return 0

        if parsed.health_action == "check":
            runtime_factory = create_read_runtime if read_only else create_runtime
            with runtime_factory(context, adapter_peer_kind="fake") as runtime:
                coordinator = runtime.health_revalidation_coordinator
                caller = require_caller_identity(LocalProcessCallerIdentityProvider()) if parsed.recover else None
                result = collect_health_check(
                    runtime.peer_registry_service,
                    runtime.health_service,
                    coordinator if parsed.recover else None,
                    caller,
                    peer=parsed.peer,
                    recover=parsed.recover,
                    now=context.clock.now(),
                )
                if parsed.json:
                    print(json.dumps(_json_safe(result)))
                else:
                    raw_peers_data = result.get("peers", ())
                    peers_data: tuple[JsonValue, ...] = (
                        raw_peers_data if isinstance(raw_peers_data, tuple) else ()
                    )
                    parts: list[str] = []
                    for p in peers_data:
                        if isinstance(p, Mapping):
                            peer_name = str(p.get("peer", ""))
                            status_val = str(p.get("status", "UNKNOWN"))
                            parts.append(f"{peer_name}={status_val}")
                    summary = " ".join(parts)
                    print(f"[HUB:GATE] HEALTH | {summary}")
                return 0

        if parsed.health_action == "precheck":
            with create_read_runtime(context, adapter_peer_kind="fake") as runtime:
                result = collect_health_precheck(
                    runtime.peer_registry_service,
                    runtime.health_service,
                    peers=parsed.peer,
                    needs=parsed.needs,
                    now=context.clock.now(),
                )
                if parsed.json:
                    print(json.dumps(_json_safe(result)))
                else:
                    scope = result.get("scope", "all")
                    if result.get("ok"):
                        print(f"[HUB] PRE-CHECK OK: scope={scope}")
                    else:
                        raw_precheck_peers = result.get("peers", ())
                        precheck_peers: tuple[JsonValue, ...] = (
                            raw_precheck_peers
                            if isinstance(raw_precheck_peers, tuple)
                            else ()
                        )
                        for p in precheck_peers:
                            if isinstance(p, Mapping) and not p.get("eligible"):
                                p_name = p.get("peer")
                                adm = p.get("admission_state")
                                avail = p.get("availability_state")
                                print(
                                    f"[HUB:WARN] Degraded peer: {p_name} "
                                    f"(admission={adm}, availability={avail})"
                                )
                        print(f"[HUB:ERROR] Governance Health Pre-Check FAILED. Scope={scope}")
                return 0 if result.get("ok") else 1

        if parsed.health_action == "sweep":
            with create_read_runtime(context, adapter_peer_kind="fake") as runtime:
                result = collect_health_sweep(
                    runtime.peer_registry_service,
                    runtime.health_service,
                    now=context.clock.now(),
                )
                if parsed.json:
                    print(json.dumps(_json_safe(result)))
                else:
                    stale_count = result.get("stale_count", 0)
                    print(f"[HUB] HEALTH-SWEEP stale={stale_count}")
                return 0

        return 0
    except (InvalidMutationError, RecordNotFoundError, ValueError, RuntimeError, PeerHubError, sqlite3.Error) as exc:
        print(f"peerhub health: {exc}", file=sys.stderr)
        return 2

def _run_ask(
    parsed: argparse.Namespace,
    *,
    caller_identity_provider: CallerIdentityProvider | None = None,
) -> int:
    from peerhub.cli.commands.daily import run_ask

    return run_ask(
        parsed,
        sys.modules[__name__],
        caller_identity_provider=caller_identity_provider,
    )

def _print_quota_table(uow: "SqliteReadUnitOfWork", peer: str | None) -> None:  # pyright: ignore[reportUnusedFunction] -- command-module compatibility seam
    projections = uow.list_usage_projections(peer)
    if not projections:
        print("No quota data recorded yet")
        return

    print(f"\n{'PEER':<10} {'POOL':<30} {'USED%':<10} {'REMAINING%':<15} {'RESETS_AT'}")
    for p in projections:
        resets_str = datetime.fromtimestamp(p.resets_at, tz=timezone.utc).isoformat() if p.resets_at else "N/A"
        print(f"{p.instance_id:<10} {p.quota_pool_scope:<30} {p.used_fraction * 100:>5.1f}%    {p.remaining_fraction * 100:>9.1f}%      {resets_str}")

def _refresh_usage_projections(  # pyright: ignore[reportUnusedFunction] -- command-module compatibility seam
    workspace_root: Path,
    *,
    force: bool,
    freshness_ttl: int = 60,
) -> list["UsageProjectionSnapshot"]:
    from peerhub.cli.commands.daily import refresh_usage_projections

    return refresh_usage_projections(
        workspace_root,
        force=force,
        freshness_ttl=freshness_ttl,
        cli=sys.modules[__name__],
    )

def _run_diag(parsed: argparse.Namespace) -> int:
    from peerhub.cli.commands.daily import run_diag

    return run_diag(parsed, sys.modules[__name__])


def _render_domain_section(domains: Mapping[str, Any]) -> str:  # pyright: ignore[reportUnusedFunction] -- command-module compatibility seam
    from peerhub.cli.commands.daily import render_domain_section

    return render_domain_section(domains)


def _guard_implicit_workspace_init(
    parsed: argparse.Namespace, paths: "PathLayout", *, creating: bool
) -> int | None:
    """Resolution must never silently become initialization (dotdir
    consolidation, ratified 2026-09-09, item 2).

    A read-only action against a missing store is empty regardless of how
    the workspace was selected. Explicit selection authorizes creation only
    for actions that actually create state.

    Returns an int when the CALLER must immediately return that value
    without doing any real work (this function already printed the
    appropriate message): `2` for a state-creating action that would
    otherwise silently initialize an implicit workspace, `0` for a
    read-only action against one (valid, just genuinely empty -- not an
    error). Returns None when it's safe to proceed normally.
    """

    if paths.database_path.exists():
        return None
    if not creating:
        return 0
    if parsed.workspace is not None:
        return None
    print(
        "peerhub: refusing to initialize a new workspace at the implicit "
        "current directory. Pass an explicit --workspace PATH, or run "
        "`peerhub workspace init` first.",
        file=sys.stderr,
    )
    return 2


def _guard_automatic_workspace_init(  # pyright: ignore[reportUnusedFunction] -- command-module compatibility seam
    parsed: argparse.Namespace,
    resolution: WorkspaceResolution,
    paths: "PathLayout",
) -> int | None:
    """Allow ask/broadcast auto-init only for a selected project."""

    if (
        paths.database_path.exists()
        or parsed.workspace is not None
        or resolution.selection_source == "env"
        or resolution.root == resolution.project_boundary
    ):
        return None
    print(
        "peerhub: no Git project or initialized workspace found at "
        f"{resolution.root}. Run `peerhub workspace init` here, or choose "
        "a project with `-w PATH`.",
        file=sys.stderr,
    )
    return 2


def _detect_workspace_home_id(database_path: Path, fallback_name: str) -> str:
    """Read the persisted workspace identity, falling back to the directory
    name. Delegates to workspace_identity.detect_workspace_home_id() (item
    10, dotdir consolidation) so the CLI bootstrap path and the backup/
    restore path share one rule instead of two copies drifting apart."""
    return detect_workspace_home_id(database_path, fallback_name)


def _run_statusline(parsed: argparse.Namespace) -> int:
    from peerhub.telemetry.statusline import format_statusline_ag
    stdin_data = ""
    if not sys.stdin.isatty():
        try:
            stdin_data = sys.stdin.read()
        except Exception:
            pass

    # item 12 (dotdir consolidation, ratified 2026-09-09): this command used
    # to also persist stdin_data to .peerhub/statusline/ag_statusline_stdin.log,
    # but nothing ever read that path -- the real, consumed statusline log
    # lives at <_sys>/data/temp/ag_statusline_stdin.log (see
    # telemetry/quota_polling.py's poll_agy_usage() and
    # telemetry/presenter.py's collect_live_snapshot()), written by the
    # agy CLI's own hook, not by this command. An orphaned durable write
    # was deleted rather than kept or relocated, per the ratified spec.
    peer = getattr(parsed, "peer", "ag")
    try:
        if peer == "ag":
            print(format_statusline_ag(stdin_data), end="")
        else:
            print(format_statusline_ag(stdin_data), end="")
    except Exception:
        print("ag:Gemini | ctx:ok | hub:idle", end="")
    return 0


def _run_status(parsed: argparse.Namespace) -> int:
    from peerhub.cli.commands.daily import run_status

    return run_status(parsed, sys.modules[__name__])


def _run_broadcast(parsed: argparse.Namespace) -> int:
    from peerhub.cli.commands.daily import run_broadcast

    return run_broadcast(parsed, sys.modules[__name__])


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
    workspace_root = resolve_workspace(parsed.workspace).root
    paths = PathLayout.for_workspace(workspace_root)
    read_only = parsed.consensus_action in ("list", "status")
    guard_code = _guard_implicit_workspace_init(
        parsed, paths, creating=not read_only
    )
    if guard_code == 0:
        if parsed.json:
            print(json.dumps({"proposals": []}))
        else:
            print("No consensus rounds.")
    if guard_code is not None:
        return guard_code
    context = RuntimeContext(
        workspace_home_id=_detect_workspace_home_id(paths.database_path, workspace_root.name),
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )
    try:
        runtime_factory = create_read_runtime if read_only else create_runtime
        with runtime_factory(context, adapter_peer_kind="fake") as runtime:
            def _resolve_dctx_actor(*, credential_id: str) -> str | None:
                import sqlite3
                from peerhub.persistence.dispatch_context import resolve_actor_for_credential
                conn = sqlite3.connect(str(context.paths.database_path))
                try:
                    row = conn.execute(
                        "SELECT workspace_home_id, activation_epoch FROM workspace_identity WHERE singleton = 1"
                    ).fetchone()
                    if row is None:
                        return None
                    workspace_home_id, activation_epoch = row
                    return resolve_actor_for_credential(
                        conn,
                        credential_id=credential_id,
                        workspace_home_id=workspace_home_id,
                        activation_epoch=activation_epoch,
                        now=context.clock.now(),
                    )
                finally:
                    conn.close()

            def _require_actor_id(explicit: str | None, credential_id: str | None) -> str:
                if explicit is not None:
                    return explicit
                if credential_id is not None:
                    resolved = _resolve_dctx_actor(credential_id=credential_id)
                    if resolved is None:
                        raise InvalidMutationError(
                            "credential does not resolve to a known actor "
                            "(invalid, expired, or revoked)"
                        )
                    return resolved
                raise InvalidMutationError(
                    "consensus vote requires --actor or a valid --credential-id"
                )

            if parsed.consensus_action == "proposal-add":
                outcome = _submit_via_gateway(runtime, ProposalAddCommand(
                    submission=_cli_submission(
                        context,
                        actor_id=parsed.from_peer,
                        request_kind="consensus-proposal-add",
                    ),
                    subject=parsed.subject,
                    from_peer=parsed.from_peer,
                    impact=parsed.impact,
                    rationale=parsed.rationale,
                    text=parsed.text,
                    verified_required=parsed.verified_required,
                ))
                if not outcome.ok:
                    print(f"peerhub consensus: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                result = cast(Mapping[str, Any], outcome.result)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                if parsed.json:
                    print(json.dumps(_json_safe({
                        "round_id": result["round_id"],
                        "from_peer": result["from_peer"],
                        "impact": result["impact"],
                        "eligible_participants": result["eligible_participants"],
                        "receipt_id": result["receipt_id"],
                        "revision": result["revision"],
                    })))
                else:
                    print(
                        f"[HUB] PROPOSAL-ADD {result['round_id']} | "
                        f"from={result['from_peer']} | "
                        f"impact={str(result['impact']).upper()}"
                    )
                    print(
                        "      Vote with: peerhub consensus proposal-vote "
                        f"--proposal-id {result['round_id']} --vote agree "
                        "--voter <peer>"
                    )
                return 0
            if parsed.consensus_action == "proposal-vote":
                if parsed.voter is not None:
                    voter_id = parsed.voter
                elif parsed.credential_id is not None:
                    resolved_voter = _resolve_dctx_actor(credential_id=parsed.credential_id)
                    if resolved_voter is None:
                        raise InvalidMutationError(
                            "credential does not resolve to a known actor "
                            "(invalid, expired, or revoked)"
                        )
                    voter_id = resolved_voter
                else:
                    voter_id = "cc"
                outcome = _submit_via_gateway(runtime, ProposalVoteCommand(
                    submission=_cli_submission(
                        context,
                        actor_id=voter_id,
                        request_kind="consensus-proposal-vote",
                    ),
                    proposal_id=parsed.proposal_id,
                    voter=voter_id,
                    vote=parsed.vote,
                    reason=parsed.reason,
                    credential_id=parsed.credential_id,
                ), credential_id=parsed.credential_id)
                if not outcome.ok:
                    print(f"peerhub consensus: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                result = cast(Mapping[str, Any], outcome.result)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                if parsed.json:
                    print(json.dumps(_json_safe({
                        "round_id": result["round_id"],
                        "voter": result["voter"],
                        "choice": result["choice"],
                        "outcome": result["outcome"],
                        "agreed": result["agreed"],
                        "disagreed": result["disagreed"],
                        "escalation_reason": result["escalation_reason"],
                        "invariant_request_target_id": result["invariant_request_target_id"],
                        "revision": result["revision"],
                    })))
                else:
                    _print_proposal_vote_compatibility(result)
                return 0
            if parsed.consensus_action == "propose":
                required = tuple(item for item in parsed.required.split(",") if item)
                eligible = tuple(item for item in parsed.eligible.split(",") if item)
                outcome = _submit_via_gateway(runtime, ConsensusProposeCommand(
                    submission=_cli_submission(
                        context,
                        actor_id=parsed.proposer,
                        request_kind="consensus-propose",
                    ),
                    round_id=parsed.round_id,
                    title=parsed.title,
                    question=parsed.question,
                    body=parsed.body,
                    proposer_id=parsed.proposer,
                    required_participants=required,
                    eligible_participants=eligible,
                    risk=parsed.risk,
                    source_hash="sha256:" + hashlib.sha256(parsed.body.encode()).hexdigest(),
                    verified_required=parsed.verified_required,
                ))
                if not outcome.ok:
                    print(f"peerhub consensus: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                target = runtime.governance_broker.get_target(target_id)
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
                # R4/P4b final domain (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client, with credential_id
                # threaded onto the envelope so GovernanceAuthorizer
                # verifies it BEFORE ConsensusService.cast_vote ever runs
                # -- see docs/design/peerhub-r4-p4b-converged-design-2026-09-17.md.
                # ConsensusService's own domain-level credential check is
                # kept as defense-in-depth (see its inline comment) rather
                # than removed, since direct/internal callers still rely
                # on it and it also enforces verified_required, which the
                # generic gateway cannot see.
                actor_id = _require_actor_id(parsed.actor, parsed.credential_id)
                outcome = _submit_via_gateway(
                    runtime,
                    ConsensusVoteCommand(
                        submission=_cli_submission(
                            context, actor_id=actor_id, request_kind="consensus-vote"
                        ),
                        round_id=parsed.round_id,
                        actor_id=actor_id,
                        choice=parsed.choice,
                        credential_id=parsed.credential_id,
                    ),
                    credential_id=parsed.credential_id,
                )
                if not outcome.ok:
                    print(f"peerhub consensus: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                target = runtime.governance_broker.get_target(target_id)
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
            if parsed.consensus_action == "arbiter-review":
                outcome = _submit_via_gateway(runtime, ArbiterReviewCommand(
                    submission=_cli_submission(
                        context,
                        actor_id=None,
                        request_kind="consensus-arbiter-review",
                    ),
                    round_id=parsed.round_id,
                ))
                if not outcome.ok:
                    print(f"peerhub consensus: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                result = cast(Mapping[str, Any], outcome.result)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                if parsed.json:
                    print(json.dumps(_json_safe(result)))
                else:
                    fired = result.get("fired")
                    reason = result.get("reason")
                    print(f"Arbiter review for round {parsed.round_id}: fired={fired}, reason={reason}")
                    if fired:
                        print(f"  Verdict: {result.get('parsed_verdict', 'unknown')}")
                        print(f"  Canonical attached: {result.get('canonical', False)}")
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
    except (
        InvalidMutationError,
        RecordNotFoundError,
        RuntimeError,
        ValueError,
        sqlite3.Error,
    ) as exc:
        print(f"peerhub consensus: {exc}", file=sys.stderr)
        return 2


def _print_proposal_vote_compatibility(result: Mapping[str, Any]) -> None:
    print(
        f"[HUB] PROPOSAL-VOTE {result['round_id']} | "
        f"{result['voter']}:{str(result['choice']).upper()}"
    )
    if result["outcome"] == "CONSENSUS_OK":
        print(
            f"[HUB] PROPOSAL CONSENSUS_OK {result['round_id']} | "
            f"unanimous agree: {','.join(cast(Sequence[str], result['agreed']))}"
        )
    elif result["outcome"] == "NACK":
        print(
            f"[HUB] PROPOSAL NACK {result['round_id']} | "
            f"disagreed: {','.join(cast(Sequence[str], result['disagreed']))}"
        )
    elif result["outcome"] == "ESCALATED":
        print(
            f"[HUB] PROPOSAL ESCALATED {result['round_id']} | "
            f"{result['escalation_reason']}"
        )


def _run_task(parsed: argparse.Namespace) -> int:
    workspace_root = resolve_workspace(parsed.workspace).root
    paths = PathLayout.for_workspace(workspace_root)
    action = parsed.task_action
    read_only = action == "status"
    guard_code = _guard_implicit_workspace_init(
        parsed, paths, creating=not read_only
    )
    if guard_code == 0:
        if parsed.json:
            print(json.dumps({"error": "not_found", "task_id": getattr(parsed, "task_id", None)}))
        else:
            print(f"Task {getattr(parsed, 'task_id', '?')}: not found (workspace uninitialized)")
    if guard_code is not None:
        return guard_code
    context = RuntimeContext(
        workspace_home_id=_detect_workspace_home_id(paths.database_path, workspace_root.name),
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )
    try:
        runtime_factory = create_read_runtime if read_only else create_runtime
        with runtime_factory(context, adapter_peer_kind="fake") as runtime:
            if action == "create":
                outcome = _submit_via_gateway(runtime, TaskCreateCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.creator, request_kind="task-create"
                    ),
                    task_id=parsed.task_id,
                    summary=parsed.summary,
                    spec=parsed.spec,
                    creator_id=parsed.creator,
                    room_id=parsed.room_id or None,
                ))
                if not outcome.ok:
                    print(f"peerhub task: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            elif action == "claim-start":
                outcome = _submit_via_gateway(runtime, TaskClaimStartCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.actor, request_kind="task-claim-start"
                    ),
                    task_id=parsed.task_id,
                    actor_id=parsed.actor,
                    request_id=parsed.request_id,
                    coordinator=parsed.coordinator,
                    attempt_id=parsed.attempt_id,
                ))
                if not outcome.ok:
                    print(f"peerhub task: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            elif action == "checkpoint":
                outcome = _submit_via_gateway(runtime, TaskCheckpointCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.actor, request_kind="task-checkpoint"
                    ),
                    task_id=parsed.task_id,
                    actor_id=parsed.actor,
                    checkpoint_id=parsed.checkpoint_id,
                    stage=parsed.stage,
                    request_id=parsed.request_id,
                    attempt_id=parsed.attempt_id,
                    resume_token_ref=parsed.resume_token or None,
                    completed_units=tuple(x for x in parsed.completed.split(",") if x),
                    remaining_units=tuple(x for x in parsed.remaining.split(",") if x),
                    expected_revision=None,
                ))
                if not outcome.ok:
                    print(f"peerhub task: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            elif action == "complete":
                outcome = _submit_via_gateway(runtime, TaskCompleteCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.actor, request_kind="task-complete"
                    ),
                    task_id=parsed.task_id,
                    actor_id=parsed.actor,
                ))
                if not outcome.ok:
                    print(f"peerhub task: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            elif action == "fail":
                outcome = _submit_via_gateway(runtime, TaskFailCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.actor, request_kind="task-fail"
                    ),
                    task_id=parsed.task_id,
                    actor_id=parsed.actor,
                    failure_class=parsed.failure_class,
                    reason=parsed.reason,
                ))
                if not outcome.ok:
                    print(f"peerhub task: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            elif action == "cancel":
                outcome = _submit_via_gateway(runtime, TaskCancelCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.actor, request_kind="task-cancel"
                    ),
                    task_id=parsed.task_id,
                    actor_id=parsed.actor,
                    reason=parsed.reason,
                ))
                if not outcome.ok:
                    print(f"peerhub task: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            else:
                target = runtime.governance_broker.get_target(parsed.task_id)
                if target is None:
                    raise RecordNotFoundError("task", parsed.task_id)
                if parsed.json:
                    print(json.dumps(_json_safe(target.state)))
                else:
                    print(f"Task {parsed.task_id}: state={target.state['state']}")
                return 0
            target = runtime.governance_broker.get_target(target_id)
            assert target is not None
            state = cast(dict[str, Any], target.state)
            payload = _json_safe(target.state)
            if parsed.json:
                print(json.dumps(payload))
            else:
                verb = {"create": "created", "claim-start": "started", "checkpoint": "checkpointed", "complete": "completed", "fail": "failed", "cancel": "cancelled"}[action]
                print(f"Task {parsed.task_id} {verb} (state={state['state']})")
            return 0
    except (
        InvalidMutationError,
        RecordNotFoundError,
        RuntimeError,
        ValueError,
        sqlite3.Error,
    ) as exc:
        print(f"peerhub task: {exc}", file=sys.stderr)
        return 2


def _run_lesson(parsed: argparse.Namespace) -> int:
    workspace_root = resolve_workspace(parsed.workspace).root
    paths = PathLayout.for_workspace(workspace_root)
    action = parsed.lesson_action
    read_only = action in ("inject", "status")
    guard_code = _guard_implicit_workspace_init(
        parsed, paths, creating=not read_only
    )
    if guard_code == 0:
        print("Workspace uninitialized; no lessons to report.")
    if guard_code is not None:
        return guard_code
    context = RuntimeContext(workspace_home_id=_detect_workspace_home_id(paths.database_path, workspace_root.name), paths=paths, clock=SystemClock(), ids=UuidSource())
    try:
        runtime_factory = create_read_runtime if read_only else create_runtime
        with runtime_factory(context, adapter_peer_kind="fake") as runtime:
            service = LessonService(runtime.governance_broker, clock=context.clock, ids=context.ids)
            if action == "propose":
                # NOT migrated (R4/P4b, 2026-09-19): the registered
                # governance.lesson.propose command's LessonProposeCommand
                # has no expires_at field, so routing through it would
                # silently drop the CLI's --expires-at support -- a real
                # functional regression, not just a routing change. Left as
                # a direct call until that command is extended.
                submission = service.propose(lesson_id=parsed.lesson_id, title=parsed.title, rule=parsed.rule, category=parsed.category, severity=parsed.severity, proposer_id=parsed.proposer, affected_peers=tuple(x for x in parsed.affected.split(",") if x), scope_kind=parsed.scope_kind, workspace_id=parsed.workspace_id, expires_at=parsed.expires_at)
                target_id = submission.receipt.target_id
            elif action == "approve":
                outcome = _submit_via_gateway(runtime, LessonApproveCommand(
                    submission=_cli_submission(
                        context,
                        actor_id=parsed.approved_by,
                        request_kind="lesson-approve",
                    ),
                    lesson_id=parsed.lesson_id,
                    approved_by_actor_id=parsed.approved_by,
                    authority_target_id=parsed.authority_target_id,
                    expected_revision=None,
                ))
                if not outcome.ok:
                    print(f"peerhub lesson: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            elif action == "activate":
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client.
                outcome = _submit_via_gateway(runtime, LessonActivateCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.actor, request_kind="lesson-activate"
                    ),
                    lesson_id=parsed.lesson_id,
                    actor_id=parsed.actor,
                    expected_revision=None,
                ))
                if not outcome.ok:
                    print(f"peerhub lesson: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            elif action == "retire":
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client.
                outcome = _submit_via_gateway(runtime, LessonRetireCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.actor, request_kind="lesson-retire"
                    ),
                    lesson_id=parsed.lesson_id,
                    actor_id=parsed.actor,
                    reason=parsed.reason,
                    expected_revision=None,
                ))
                if not outcome.ok:
                    print(f"peerhub lesson: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            elif action == "supersede":
                outcome = _submit_via_gateway(runtime, LessonSupersedeCommand(
                    submission=_cli_submission(
                        context,
                        actor_id=parsed.actor,
                        request_kind="lesson-supersede",
                    ),
                    lesson_id=parsed.lesson_id,
                    actor_id=parsed.actor,
                    replacement_lesson_id=parsed.replacement_lesson_id,
                    expected_revision=None,
                ))
                if not outcome.ok:
                    print(f"peerhub lesson: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            elif action == "quarantine":
                outcome = _submit_via_gateway(runtime, LessonQuarantineCommand(
                    submission=_cli_submission(
                        context,
                        actor_id=parsed.actor,
                        request_kind="lesson-quarantine",
                    ),
                    lesson_id=parsed.lesson_id,
                    actor_id=parsed.actor,
                    reason=parsed.reason,
                    evidence=parsed.evidence,
                    expected_revision=None,
                ))
                if not outcome.ok:
                    print(f"peerhub lesson: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            elif action == "sweep":
                outcome = _submit_via_gateway(runtime, LessonSweepCommand(
                    submission=_cli_submission(
                        context,
                        actor_id="peerhub-lesson-sweep",
                        request_kind="lesson-sweep",
                    ),
                    actor_id="peerhub-lesson-sweep",
                ))
                if not outcome.ok:
                    print(f"peerhub lesson: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                sweep_result = cast(Mapping[str, JsonValue], outcome.result)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                retired_ids = cast("list[str]", sweep_result["retired"])
                if parsed.json:
                    print(json.dumps(_json_safe({"retired": retired_ids})))
                else:
                    print(f"LESSON-SWEEP retired={len(retired_ids)} {','.join(retired_ids) if retired_ids else '(none)'}")
                return 0
            elif action == "inject":
                from peerhub.application.lesson_inject import inject_lessons, LessonInjectionContext, LessonInjectionPolicy
                
                os_val: str | None = getattr(parsed, "os", None)
                shell_val: str | None = getattr(parsed, "shell", None)
                task_types_str: str | None = getattr(parsed, "task_types", None)
                tasks: frozenset[str] = (
                    frozenset(x for x in task_types_str.split(",") if x)
                    if task_types_str
                    else frozenset()
                )
                
                # Determine workspace_id. CLI often passes workspace_home_id or we use "default"
                ws_id = getattr(parsed, "workspace_id", None)
                if not ws_id:
                    ws_id = context.workspace_home_id or "default"
                    
                ctx = LessonInjectionContext(os=os_val, shell=shell_val, task_types=tasks)
                policy = LessonInjectionPolicy()
                result = inject_lessons(broker=runtime.governance_broker, target_peer_id=parsed.target_peer, workspace_id=ws_id, context=ctx, policy=policy)
                
                if result:
                    print(result)
                else:
                    print(f"[HUB] No active lessons for peer={parsed.target_peer}")
                return 0
            elif action == "broadcast":
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client.
                outcome = _submit_via_gateway(runtime, LessonBroadcastCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.sender_profile_id, request_kind="lesson-broadcast"
                    ),
                    lesson_id=parsed.lesson_id,
                    room_id=parsed.room_id,
                    sender_instance_id=parsed.sender_instance_id,
                    sender_profile_id=parsed.sender_profile_id,
                ))
                if not outcome.ok:
                    print(f"peerhub lesson: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                payload = cast(Mapping[str, JsonValue], outcome.result)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                recipient_profile_ids = cast(
                    "list[str]", payload["recipient_profile_ids"]
                )
                if parsed.json:
                    print(json.dumps(_json_safe(payload)))
                elif recipient_profile_ids:
                    print(
                        f"LESSON-BROADCAST {parsed.lesson_id} -> "
                        f"{','.join(recipient_profile_ids)}"
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
            target = runtime.governance_broker.get_target(target_id)
            assert target is not None
            state = cast(dict[str, Any], target.state)
            if parsed.json:
                print(json.dumps(_json_safe(target.state)))
            else:
                verb = {"propose": "proposed", "approve": "approved", "activate": "activated", "retire": "retired", "supersede": "superseded", "quarantine": "quarantined"}[action]
                print(f"Lesson {parsed.lesson_id} {verb} (lifecycle={state['lifecycle']})")
            return 0
    except (InvalidMutationError, RecordNotFoundError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"peerhub lesson: {exc}", file=sys.stderr)
        return 2


def _run_directive(parsed: argparse.Namespace) -> int:
    workspace_root = resolve_workspace(parsed.workspace).root
    paths = PathLayout.for_workspace(workspace_root)
    read_only = parsed.directive_action == "list"
    guard_code = _guard_implicit_workspace_init(
        parsed, paths, creating=not read_only
    )
    if guard_code is not None:
        return guard_code
    context = RuntimeContext(workspace_home_id=_detect_workspace_home_id(paths.database_path, workspace_root.name), paths=paths, clock=SystemClock(), ids=UuidSource())
    try:
        runtime_factory = create_read_runtime if read_only else create_runtime
        with runtime_factory(context, adapter_peer_kind="fake") as runtime:
            service = runtime.directive_service
            action = parsed.directive_action
            if action == "add":
                submission = service.propose(
                    directive_id=parsed.directive_id, 
                    title=parsed.title, 
                    rule=parsed.rule, 
                    effective_date=parsed.effective_date, 
                    proposer_id=parsed.proposer, 
                    category=getattr(parsed, "category", None)
                )
            elif action == "migrate":
                consumers: list[dict[str, JsonValue]] = json.loads(parsed.consumers) if getattr(parsed, "consumers", None) else []
                submission = service.migrate(
                    directive_id=parsed.directive_id,
                    title=parsed.title,
                    rule_markdown=parsed.rule,
                    digest=parsed.digest,
                    consumers=consumers,
                    source_path=parsed.source_path,
                    migrated_by=parsed.actor
                )
            elif action == "clear":
                submission = service.retire(
                    directive_id=parsed.directive_id, 
                    actor_id=parsed.actor, 
                    reason=parsed.reason
                )
            elif action == "list":
                targets = service.list_all()
                if getattr(parsed, "json", False):
                    print(json.dumps([{"directive_id": t.target_id, "state": dict(t.state)} for t in targets]))
                else:
                    for t in targets:
                        state_dict = dict(t.state)
                        lifecycle = state_dict.get('lifecycle')
                        content = state_dict.get('content')
                        title = content.get('title') if isinstance(content, dict) else ""
                        print(f"{t.target_id}: {lifecycle} - {title}")
                return 0
            else:
                raise AssertionError(f"unhandled directive action: {action!r}")

            target = runtime.governance_broker.get_target(submission.receipt.target_id)
            assert target is not None
            state = cast(dict[str, Any], target.state)
            if getattr(parsed, "json", False):
                print(json.dumps(_json_safe(target.state)))
            else:
                verb = {"add": "proposed", "migrate": "migrated", "clear": "retired"}[action]
                print(f"Directive {parsed.directive_id} {verb} (lifecycle={state['lifecycle']})")
            return 0
    except (InvalidMutationError, RecordNotFoundError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"peerhub directive: {exc}", file=sys.stderr)
        return 2


def _run_node(parsed: argparse.Namespace) -> int:
    workspace_root = resolve_workspace(parsed.workspace).root
    paths = PathLayout.for_workspace(workspace_root)
    read_only = parsed.node_action in (None, "list", "model-status")
    guard_code = _guard_implicit_workspace_init(
        parsed, paths, creating=not read_only
    )
    if guard_code == 0:
        if parsed.json:
            print(json.dumps({"nodes": []}))
        else:
            print("No nodes registered.")
    if guard_code is not None:
        return guard_code
    context = RuntimeContext(workspace_home_id=_detect_workspace_home_id(paths.database_path, workspace_root.name), paths=paths, clock=SystemClock(), ids=UuidSource())
    try:
        runtime_factory = create_read_runtime if read_only else create_runtime
        with runtime_factory(context, adapter_peer_kind="fake") as runtime:
            service = runtime.peer_registry_service
            if parsed.node_action == "register":
                submission = service.register_node(
                    node_id=parsed.node_id,
                    peer_kind=parsed.peer_kind,
                    profile_id=parsed.profile_id,
                    tier=parsed.tier,
                    node_type=parsed.node_type,
                    actor_id=parsed.actor,
                )
                target = runtime.governance_broker.get_target(submission.receipt.target_id)
                assert target is not None
                if parsed.json:
                    print(json.dumps(_json_safe(target.state)))
                else:
                    print(f"Node {parsed.node_id} registered (peer_kind={target.state['peer_kind']}, profile_id={target.state['profile_id']})")
                return 0
            if parsed.node_action == "bind-profile":
                submission = service.bind_profile(
                    node_id=parsed.node_id,
                    profile_id=parsed.profile_id,
                    model_id=parsed.model_id,
                    reasoning_effort=parsed.reasoning_effort,
                    selection_mode=parsed.selection_mode,
                    actor_id=parsed.actor,
                )
                target = runtime.governance_broker.get_target(
                    submission.receipt.target_id
                )
                assert target is not None
                if parsed.json:
                    print(json.dumps(_json_safe(target.state)))
                else:
                    effort = target.state.get("reasoning_effort") or ""
                    model_display = (
                        "(cli default)"
                        if parsed.selection_mode == "cli_default"
                        else parsed.model_id
                    )
                    print(
                        f"Profile {parsed.node_id}/{parsed.profile_id} bound "
                        f"to model={model_display}, effort={effort}"
                    )
                return 0
            if parsed.node_action == "model-status":
                rows = collect_model_status(service, runtime.health_service)
                if parsed.json:
                    print(json.dumps(_json_safe({"models": rows})))
                else:
                    print(
                        "peer\tstatus\tprofile\tmodel\teffort\tcost\t"
                        "context\tcapabilities"
                    )
                    for row in rows:
                        print(
                            "\t".join(
                                str(row.get(field, ""))
                                for field in (
                                    "peer",
                                    "status",
                                    "profile",
                                    "model",
                                    "effort",
                                    "cost",
                                    "context",
                                    "capabilities",
                                )
                            )
                        )
                return 0
            nodes = service.list_nodes()
            if parsed.json:
                print(json.dumps(_json_safe({"nodes": [{"target_id": n.target_id, "revision": n.revision, "state": n.state} for n in nodes]})))
            elif not nodes:
                print("No nodes registered.")
            else:
                print("Nodes:")
                for n in nodes:
                    state = cast(Mapping[str, Any], n.state)
                    print(f"{state['node_id']}: peer_kind={state['peer_kind']}, profile_id={state['profile_id']}, source={state.get('source', 'registered')}")
            return 0
    except (InvalidMutationError, RecordNotFoundError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"peerhub node: {exc}", file=sys.stderr)
        return 2


def _run_peer(parsed: argparse.Namespace) -> int:
    from peerhub.application.peer_registry import collect_peer_status
    from peerhub.application.health_revalidation import (
        execute_peer_quarantine,
        execute_peer_recover,
    )

    workspace_root = resolve_workspace(parsed.workspace).root
    paths = PathLayout.for_workspace(workspace_root)
    read_only = parsed.peer_action == "status"
    guard_code = _guard_implicit_workspace_init(
        parsed, paths, creating=not read_only
    )
    if guard_code == 0:
        if parsed.json:
            print(json.dumps({"peers": []}))
        else:
            print("PEER\tLIFECYCLE\tGATE\tHEALTH\tVERSION\tDETAILS")
    if guard_code is not None:
        return guard_code
    context = RuntimeContext(
        workspace_home_id=_detect_workspace_home_id(
            paths.database_path, workspace_root.name
        ),
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )
    try:
        if parsed.peer_action == "status":
            with create_read_runtime(context, adapter_peer_kind="fake") as runtime:
                rows = collect_peer_status(
                    runtime.peer_registry_service,
                    runtime.health_service,
                    node_id=parsed.peer,
                    include_all=parsed.all,
                    now=context.clock.now(),
                )
                if parsed.json:
                    print(json.dumps(_json_safe({"peers": rows})))
                else:
                    print("PEER\tLIFECYCLE\tGATE\tHEALTH\tVERSION\tDETAILS")
                    for row in rows:
                        print(
                            "\t".join(
                                str(row.get(field, ""))
                                for field in (
                                    "peer",
                                    "lifecycle",
                                    "gate",
                                    "health",
                                    "version",
                                    "details",
                                )
                            )
                        )
                return 0

        if parsed.peer_action == "quarantine":
            with create_runtime(context, adapter_peer_kind="fake") as runtime:
                caller = require_caller_identity(
                    LocalProcessCallerIdentityProvider()
                )
                actor_id = parsed.actor or caller.principal_id
                result = execute_peer_quarantine(
                    runtime.peer_registry_service,
                    runtime.health_service,
                    peer_id=parsed.peer,
                    reason=parsed.reason,
                    actor_id=actor_id,
                    now=context.clock.now(),
                )
                if parsed.json:
                    print(json.dumps(_json_safe(result)))
                else:
                    peer_id = result.get("peer")
                    reason = result.get("reason")
                    print(f"[HUB] PEER-QUARANTINE {peer_id} | reason={reason}")
                return 0

        if parsed.peer_action == "recover":
            with create_runtime(context, adapter_peer_kind="fake") as runtime:
                caller = require_caller_identity(
                    LocalProcessCallerIdentityProvider()
                )
                result = execute_peer_recover(
                    runtime.peer_registry_service,
                    runtime.health_revalidation_coordinator,
                    caller,
                    peer_id=parsed.peer,
                    reason=parsed.reason,
                    now=context.clock.now(),
                )
                if parsed.json:
                    print(json.dumps(_json_safe(result)))
                else:
                    raw_results = result.get("results", ())
                    results: tuple[JsonValue, ...] = (
                        raw_results if isinstance(raw_results, tuple) else ()
                    )
                    if len(results) == 1:
                        item = results[0]
                        if isinstance(item, Mapping) and item.get("status") == "OK":
                            print(
                                f"Revalidation outcome for {item.get('peer')}: "
                                f"probe={item.get('probe_outcome')}, "
                                f"admission={item.get('admission_state')}, "
                                f"availability={item.get('availability_state')}, "
                                f"circuit_closed={item.get('circuit_closed')}"
                            )
                        elif isinstance(item, Mapping):
                            print(
                                f"Recovery failed for {item.get('peer')}: "
                                f"{item.get('error')}",
                                file=sys.stderr,
                            )
                            return 2
                    else:
                        print("PEER\tPROBE\tADMISSION\tAVAILABILITY\tCIRCUIT_CLOSED\tSTATUS")
                        for item in results:
                            if isinstance(item, Mapping):
                                print(
                                    f"{item.get('peer')}\t"
                                    f"{item.get('probe_outcome', '')}\t"
                                    f"{item.get('admission_state', '')}\t"
                                    f"{item.get('availability_state', '')}\t"
                                    f"{item.get('circuit_closed', '')}\t"
                                    f"{item.get('status')}"
                                )
                return 0
        return 0
    except (InvalidMutationError, RecordNotFoundError, ValueError, RuntimeError, PeerHubError) as exc:
        print(f"peerhub peer: {exc}", file=sys.stderr)
        return 2


def _format_lease_timestamp(timestamp_ms: object) -> str:
    if not isinstance(timestamp_ms, int):
        return ""
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()


def _run_broker(parsed: argparse.Namespace) -> int:
    workspace_root = resolve_workspace(parsed.workspace).root
    paths = PathLayout.for_workspace(workspace_root)
    guard_code = _guard_implicit_workspace_init(parsed, paths, creating=False)
    if guard_code == 0:
        print("[HUB] No unfinished governance effect deliveries.")
    if guard_code is not None:
        return guard_code
    context = RuntimeContext(
        workspace_home_id=_detect_workspace_home_id(
            paths.database_path, workspace_root.name
        ),
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )
    try:
        with create_read_runtime(context, adapter_peer_kind="fake") as runtime:
            result = collect_effect_status(
                runtime.governance_broker,
                limit=parsed.limit,
            )
            if parsed.json:
                print(json.dumps(_json_safe(result)))
                return 0

            deliveries = result["deliveries"]
            assert isinstance(deliveries, tuple)
            if not deliveries:
                print("[HUB] No unfinished governance effect deliveries.")
                return 0

            print("EVENT_ID\tSTATE\tDISPOSITION\tEFFECT_KIND\tTARGET_ID\tREVISION")
            for delivery in deliveries:
                assert isinstance(delivery, Mapping)
                print(
                    f"{delivery.get('event_id')}\t"
                    f"{delivery.get('outbox_state')}\t"
                    f"{delivery.get('recovery_disposition')}\t"
                    f"{delivery.get('effect_kind')}\t"
                    f"{delivery.get('target_id')}\t"
                    f"{delivery.get('target_revision')}"
                )
            suffix = "+" if result["has_more"] else ""
            print(
                f"Visible unfinished deliveries: "
                f"{result['visible_unfinished_count']}{suffix}"
            )
            return 0
    except (ValueError, RuntimeError, PeerHubError, sqlite3.Error) as exc:
        print(f"peerhub broker: {exc}", file=sys.stderr)
        return 2


def _run_lease(parsed: argparse.Namespace) -> int:
    from peerhub.application.lease_status import collect_lease_status

    workspace_root = resolve_workspace(parsed.workspace).root
    paths = PathLayout.for_workspace(workspace_root)
    read_only = parsed.lease_action == "status"
    guard_code = _guard_implicit_workspace_init(
        parsed, paths, creating=not read_only
    )
    if guard_code == 0:
        print("[HUB] No active leases.")
    if guard_code is not None:
        return guard_code
    context = RuntimeContext(
        workspace_home_id=_detect_workspace_home_id(
            paths.database_path, workspace_root.name
        ),
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )
    try:
        runtime_factory = create_read_runtime if read_only else create_runtime
        with runtime_factory(context, adapter_peer_kind="fake") as runtime:
            if parsed.lease_action == "sweep":
                caller = require_caller_identity(
                    LocalProcessCallerIdentityProvider()
                )
                report = runtime.process_lease_sweep_coordinator.sweep(
                    recovery_actor_principal_id=caller.principal_id,
                    limit=parsed.limit,
                    reap=not parsed.no_reap,
                )
                swept: tuple[JsonValue, ...] = tuple({
                    "lease_id": item.lease_id,
                    "profile_id": item.profile_id,
                    "pre_state": item.pre_state.value,
                    "post_state": item.post_state.value,
                    "process_alive": item.process_alive,
                    "process_identity_matches": (
                        item.process_identity_matches
                    ),
                    "actual_process_creation_time": (
                        item.actual_process_creation_time
                    ),
                    "recovery_receipt_id": item.recovery_receipt_id,
                    "recovery_decision": item.recovery_decision.value,
                    "reaped": item.reaped,
                    "reap_signal": item.reap_signal,
                    "backoff_duration_seconds": (
                        item.backoff_duration_seconds
                    ),
                } for item in report.swept)
                payload: Mapping[str, JsonValue] = {
                    "sweep_id": report.sweep_id,
                    "as_of": report.as_of,
                    "swept": swept,
                }
                if parsed.json:
                    print(json.dumps(_json_safe(payload)))
                elif not swept:
                    print("[HUB] No expired leases.")
                else:
                    print(
                        "LEASE\tPROFILE\tPRE\tPOST\tALIVE\tREAPED\tBACKOFF_SECONDS"
                    )
                    for item in swept:
                        assert isinstance(item, Mapping)
                        print(
                            f"{item.get('lease_id')}\t"
                            f"{item.get('profile_id')}\t"
                            f"{item.get('pre_state')}\t"
                            f"{item.get('post_state')}\t"
                            f"{item.get('process_alive')}\t"
                            f"{item.get('reaped')}\t"
                            f"{item.get('backoff_duration_seconds')}"
                        )
                return 0

            rows = collect_lease_status(
                runtime.dispatch_service, now=context.clock.now()
            )
            if parsed.json:
                print(json.dumps(_json_safe({"leases": rows})))
            elif not rows:
                print("[HUB] No active leases.")
            else:
                print(
                    f"{'Peer':<8} {'Status':<10} {'PID':<8} {'Alive':<6} "
                    f"{'Expires':<20} {'Heartbeat':<20}"
                )
                print("-" * 78)
                for row in rows:
                    print(
                        f"{str(row['peer']):<8} {str(row['status']):<10} "
                        f"{str(row['pid'] or ''):<8} {str(row['alive']):<6} "
                        f"{_format_lease_timestamp(row['expires_at']):<20} "
                        f"{_format_lease_timestamp(row['heartbeat_at']):<20}"
                    )
            return 0
    except (InvalidMutationError, RecordNotFoundError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"peerhub lease: {exc}", file=sys.stderr)
        return 2


def _run_gate(parsed: argparse.Namespace) -> int:
    from peerhub.application.health_revalidation import collect_check_gate

    workspace_root = resolve_workspace(parsed.workspace).root
    paths = PathLayout.for_workspace(workspace_root)
    guard_code = _guard_implicit_workspace_init(parsed, paths, creating=False)
    if guard_code == 0:
        print("Workspace uninitialized; no gate state to report.")
    if guard_code is not None:
        return guard_code
    context = RuntimeContext(
        workspace_home_id=_detect_workspace_home_id(
            paths.database_path, workspace_root.name
        ),
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )
    try:
        with create_read_runtime(context, adapter_peer_kind="fake") as runtime:
            result = collect_check_gate(
                runtime.peer_registry_service,
                runtime.health_service,
                agent=parsed.agent,
                now=context.clock.now(),
            )
            if parsed.json:
                print(json.dumps(_json_safe(result)))
            else:
                agent = result.get("agent")
                gate = result.get("gate")
                print(f"[GATE] {agent}={gate}")
            return 0 if result.get("open") else 1
    except (InvalidMutationError, RecordNotFoundError, ValueError, RuntimeError, PeerHubError, sqlite3.Error) as exc:
        print(f"peerhub gate: {exc}", file=sys.stderr)
        return 2


def _run_role(parsed: argparse.Namespace) -> int:
    workspace_root = resolve_workspace(parsed.workspace).root
    paths = PathLayout.for_workspace(workspace_root)
    read_only = parsed.role_action == "status"
    guard_code = _guard_implicit_workspace_init(
        parsed, paths, creating=not read_only
    )
    if guard_code == 0:
        print("No roles assigned.")
    if guard_code is not None:
        return guard_code
    context = RuntimeContext(
        workspace_home_id=_detect_workspace_home_id(
            paths.database_path, workspace_root.name
        ),
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )
    try:
        runtime_factory = create_read_runtime if read_only else create_runtime
        with runtime_factory(context, adapter_peer_kind="fake") as runtime:
            service = runtime.role_assignment_service
            if parsed.role_action == "assign":
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client.
                outcome = _submit_via_gateway(runtime, AssignRoleCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.actor, request_kind="role-assign"
                    ),
                    role=parsed.role,
                    peer_node_id=parsed.peer_node_id,
                    actor_id=parsed.actor,
                ))
                if not outcome.ok:
                    print(f"peerhub role: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                target = runtime.governance_broker.get_target(target_id)
                assert target is not None
                if parsed.json:
                    print(json.dumps(_json_safe(target.state)))
                else:
                    print(
                        f"Role {parsed.role} assigned to "
                        f"{parsed.peer_node_id}"
                    )
                return 0
            if parsed.role_action == "release":
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client.
                outcome = _submit_via_gateway(runtime, ReleaseRoleCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.actor, request_kind="role-release"
                    ),
                    role=parsed.role,
                    actor_id=parsed.actor,
                    peer_node_id=parsed.peer_node_id,
                ))
                if not outcome.ok:
                    print(f"peerhub role: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                result = cast(Mapping[str, JsonValue], outcome.result)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                if parsed.json:
                    print(json.dumps(_json_safe(result)))
                elif result["disposition"] == RoleReleaseDisposition.NOT_ASSIGNED.value:
                    print(f"Warning: role {parsed.role} is not assigned.")
                else:
                    print(f"Role {parsed.role} released.")
                return 0

            roles = service.list_roles()
            payload = {
                "roles": [
                    {
                        "target_id": target.target_id,
                        "revision": target.revision,
                        "state": target.state,
                    }
                    for target in roles
                ]
            }
            if parsed.json:
                print(json.dumps(_json_safe(payload)))
            elif not roles:
                print("No roles assigned.")
            else:
                print("Role assignments:")
                for target in roles:
                    print(
                        f"{target.state['role']}: "
                        f"{target.state['peer_node_id']}"
                    )
            return 0
    except (InvalidMutationError, RecordNotFoundError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"peerhub role: {exc}", file=sys.stderr)
        return 2


def _run_leadership(parsed: argparse.Namespace) -> int:
    workspace_root = resolve_workspace(parsed.workspace).root
    paths = PathLayout.for_workspace(workspace_root)
    read_only = parsed.leadership_action == "status"
    guard_code = _guard_implicit_workspace_init(
        parsed, paths, creating=not read_only
    )
    if guard_code == 0:
        print("No leadership record.")
    if guard_code is not None:
        return guard_code
    context = RuntimeContext(
        workspace_home_id=_detect_workspace_home_id(
            paths.database_path, workspace_root.name
        ),
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )
    try:
        runtime_factory = create_read_runtime if read_only else create_runtime
        with runtime_factory(context, adapter_peer_kind="fake") as runtime:
            service = runtime.leadership_service
            if parsed.leadership_action == "claim":
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client. Wire shape is the
                # registered command's own (flat: disposition/status/term/
                # challenge_until alongside receipt fields), not the old
                # CLI-local nested {"disposition":, "target": {...}} shape.
                outcome = _submit_via_gateway(runtime, LeaderClaimCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.actor, request_kind="leadership-claim"
                    ),
                    peer_node_id=parsed.peer_node_id,
                    actor_id=parsed.actor,
                    reason=parsed.reason,
                    domain=parsed.domain,
                ))
                if not outcome.ok:
                    print(f"peerhub leadership: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                claim_result = cast(Mapping[str, JsonValue], outcome.result)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                if parsed.json:
                    print(json.dumps(_json_safe(claim_result)))
                else:
                    print(
                        f"Leadership claimed by {parsed.peer_node_id} "
                        f"(status={claim_result['status']}, "
                        f"disposition={claim_result['disposition']}, "
                        f"challenge_until={claim_result['challenge_until']})"
                    )
                return 0

            if parsed.leadership_action == "yield":
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client.
                outcome = _submit_via_gateway(runtime, LeaderYieldCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.actor, request_kind="leadership-yield"
                    ),
                    yielding_peer_id=parsed.peer_node_id,
                    actor_id=parsed.actor,
                    reason=parsed.reason,
                ))
                if not outcome.ok:
                    print(f"peerhub leadership: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                yield_result = cast(Mapping[str, JsonValue], outcome.result)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                owner_mismatch = yield_result["owner_mismatch"]
                previous_leader_peer_node_id = yield_result["previous_leader_peer_node_id"]
                if parsed.json:
                    print(json.dumps(_json_safe({
                        "owner_mismatch": owner_mismatch,
                        "previous_leader_peer_node_id": previous_leader_peer_node_id,
                    })))
                else:
                    if owner_mismatch:
                        print(
                            f"Warning: {parsed.peer_node_id} yielded "
                            f"leadership, but the current leader is "
                            f"{previous_leader_peer_node_id}.",
                            file=sys.stderr,
                        )
                    print(
                        f"Leadership yielded by {parsed.peer_node_id} "
                        f"(status=VACANT)"
                    )
                return 0

            target = service.get_leadership()
            if parsed.json:
                payload = None if target is None else {
                    "target_id": target.target_id,
                    "revision": target.revision,
                    "state": target.state,
                }
                print(json.dumps(_json_safe({"leadership": payload})))
            elif target is None:
                print("No leadership record.")
            else:
                state = cast(Mapping[str, Any], target.state)
                leader = cast(
                    "Mapping[str, Any] | None", state.get("leader")
                )
                holder = (
                    "-" if leader is None else str(leader.get("peer_node_id"))
                )
                print("status\tpeer_node_id\tterm\tchallenge_until")
                print(
                    f"{state['status']}\t{holder}\t{state['term']}\t"
                    f"{state['challenge_until']}"
                )
            return 0
    except (InvalidMutationError, RecordNotFoundError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"peerhub leadership: {exc}", file=sys.stderr)
        return 2


def _run_routing(parsed: argparse.Namespace) -> int:
    from peerhub.application.capability_config import (
        import_legacy_capability_configs,
    )
    from peerhub.application.capability_matching import (
        encode_capability_ranking,
        encode_leadership_election_receipt,
    )

    workspace_root = resolve_workspace(parsed.workspace).root
    paths = PathLayout.for_workspace(workspace_root)
    read_only = parsed.routing_action == "discover"
    guard_code = _guard_implicit_workspace_init(
        parsed, paths, creating=not read_only
    )
    if guard_code == 0:
        print("Workspace uninitialized; no routing state to report.")
    if guard_code is not None:
        return guard_code
    context = RuntimeContext(
        workspace_home_id=_detect_workspace_home_id(
            paths.database_path, workspace_root.name
        ),
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )
    try:
        runtime_factory = create_read_runtime if read_only else create_runtime
        with runtime_factory(context, adapter_peer_kind="fake") as runtime:
            if parsed.routing_action == "import-capabilities":
                sys_root = workspace_root.parent.parent / "_sys" / "ai"
                protocol_path = Path(
                    parsed.protocol or sys_root / "protocol.json"
                ).resolve()
                orchestration_path = Path(
                    parsed.orchestration or sys_root / "orchestration.json"
                ).resolve()
                result = import_legacy_capability_configs(
                    runtime.capability_config_service,
                    protocol_path=protocol_path,
                    orchestration_path=orchestration_path,
                )
                payload = {
                    "configs": tuple(
                        {
                            "target_id": config.target_id,
                            "revision": config.revision,
                            "node_id": config.node_id,
                            "enabled": config.enabled,
                            "aliases": config.aliases,
                            "capabilities": tuple(
                                {
                                    "name": capability.name,
                                    "sources": capability.sources,
                                }
                                for capability in config.capabilities
                            ),
                        }
                        for config in result.configs
                    ),
                    "policy_target_id": result.policy.target_id,
                    "policy_revision": result.policy.target_revision,
                }
                print(json.dumps(_json_safe(payload)))
                return 0

            coordinator = runtime.capability_matching_coordinator
            if parsed.routing_action == "discover":
                ranking = coordinator.discover(
                    needs=parsed.needs,
                    effort=parsed.effort,
                )
                payload = encode_capability_ranking(ranking)
                if parsed.json:
                    print(json.dumps(_json_safe(payload)))
                elif ranking.ordered_matches:
                    for match in ranking.ordered_matches:
                        print(
                            f"{match.node_id}\tscore={match.ranking_score}\t"
                            f"health={_enum_value(match.availability_status)}"
                        )
                else:
                    fallback = ranking.fallback
                    print(
                        "No matching peers; fallback="
                        f"{None if fallback is None else fallback.node_id}"
                    )
                return 0

            receipt = coordinator.elect_leader(
                needs=parsed.needs,
                effort=parsed.effort,
                reason=parsed.reason,
                actor_id=parsed.actor,
            )
            payload = encode_leadership_election_receipt(receipt)
            if parsed.json:
                print(json.dumps(_json_safe(payload)))
            else:
                print(
                    f"Elected {receipt.selected_node_id} "
                    f"({receipt.selection_basis}); "
                    f"claim={receipt.leadership_claim_id}"
                )
            return 0
    except (InvalidMutationError, RecordNotFoundError, ValueError, RuntimeError, PeerHubError, sqlite3.Error) as exc:
        print(f"peerhub routing: {exc}", file=sys.stderr)
        return 2


def _run_feedback(parsed: argparse.Namespace) -> int:
    workspace_root = resolve_workspace(parsed.workspace).root
    paths = PathLayout.for_workspace(workspace_root)
    read_only = parsed.feedback_action == "list"
    guard_code = _guard_implicit_workspace_init(
        parsed, paths, creating=not read_only
    )
    if guard_code == 0:
        print("No feedback records found.")
    if guard_code is not None:
        return guard_code
    context = RuntimeContext(
        workspace_home_id=_detect_workspace_home_id(
            paths.database_path, workspace_root.name
        ),
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )
    try:
        runtime_factory = create_read_runtime if read_only else create_runtime
        with runtime_factory(context, adapter_peer_kind="fake") as runtime:
            service = runtime.feedback_service
            if parsed.feedback_action == "add":
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client, not a direct
                # FeedbackService call.
                outcome = _submit_via_gateway(runtime, FeedbackAddCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.actor, request_kind="feedback-add"
                    ),
                    source_peer=parsed.source_peer,
                    category=parsed.category,
                    severity=parsed.severity,
                    title=parsed.title,
                    detail=parsed.detail,
                    actor_id=parsed.actor,
                ))
                if not outcome.ok:
                    print(f"peerhub feedback: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                target = runtime.governance_broker.get_target(target_id)
                assert target is not None
                if parsed.json:
                    print(json.dumps(_json_safe(target.state)))
                else:
                    print(
                        f"Feedback {target.state['feedback_id']} added "
                        f"(peer={target.state['source_peer']}, "
                        f"title={target.state['title']})"
                    )
                return 0

            if parsed.feedback_action == "resolve":
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client, not a direct
                # FeedbackService call.
                outcome = _submit_via_gateway(runtime, FeedbackResolveCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.actor, request_kind="feedback-resolve"
                    ),
                    feedback_id=parsed.feedback_id,
                    status=parsed.status,
                    owner=parsed.owner,
                    actor_id=parsed.actor,
                ))
                if not outcome.ok:
                    print(f"peerhub feedback: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                target = runtime.governance_broker.get_target(target_id)
                assert target is not None
                if parsed.json:
                    print(json.dumps(_json_safe(target.state)))
                else:
                    print(
                        f"Feedback {parsed.feedback_id} resolved "
                        f"(status={target.state['status']})"
                    )
                return 0

            items = service.list_feedback()
            if parsed.json:
                print(json.dumps(_json_safe({
                    "feedback": [
                        {
                            "target_id": item.target_id,
                            "revision": item.revision,
                            "state": item.state,
                        }
                        for item in items
                    ]
                })))
            elif not items:
                print("No feedback records found.")
            else:
                print("id\tstatus\tseverity\tcategory\ttitle")
                for item in items:
                    state = cast(Mapping[str, Any], item.state)
                    print(
                        f"{state['feedback_id']}\t{state['status']}\t"
                        f"{state['severity']}\t{state['category']}\t"
                        f"{state['title']}"
                    )
            return 0
    except (InvalidMutationError, RecordNotFoundError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"peerhub feedback: {exc}", file=sys.stderr)
        return 2


_GATEWAY_CLIENT_ID = "peerhub-cli"
"""R4/P4b (docs/design/peerhub-r4-p4b-converged-design-2026-09-17.md):
stable client_id used by every CLI entrance migrated onto the
ApplicationAPI gateway, for both the RequestContext (caller) and the
SubmissionMetadata (submission) built for a given command -- these two
must match for the gateway's asserted-path GovernanceAuthorizer check to
pass, since the CLI presents no D-CTX credential of its own."""


def _cli_submission(
    context: RuntimeContext, *, actor_id: str | None, request_kind: str
) -> SubmissionMetadata:
    """Build SubmissionMetadata for a gateway-routed CLI command.

    ``request_kind`` is a short label (e.g. "error-report") used only to
    make generated request/correlation/idempotency IDs recognizable in
    logs -- it carries no semantic meaning to the gateway itself."""

    return SubmissionMetadata(
        client_request_id=context.ids.new_id(f"{request_kind}-request"),
        correlation_id=context.ids.new_id(f"{request_kind}-correlation"),
        client_id=_GATEWAY_CLIENT_ID,
        actor_id=actor_id,
        scope={},
        idempotency_key=context.ids.new_id(f"{request_kind}-idempotency"),
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=context.clock.now(),
    )


def _submit_via_gateway(
    runtime: "Runtime",
    command: "Command[Any]",
    *,
    credential_id: str | None = None,
) -> "CommandOutcome[Any]":
    """Submit one command through ApplicationAPI.submit() (R4/P4b gateway).

    Without ``credential_id``: a locally-authenticated CLI caller
    asserting its own actor_id (no D-CTX credential presented -- see
    GovernanceAuthorizer's asserted path). With ``credential_id``: the
    gateway verifies it against the command's actor_id via
    GovernanceAuthorizer's verified path before the domain handler runs
    at all (currently only `consensus vote` presents one)."""

    from peerhub.core.identity import AuthenticatedSubject

    principal = command.submission.actor_id or _GATEWAY_CLIENT_ID
    client = Client(
        runtime.application_api,
        caller=RequestContext(
            principal=AuthenticatedSubject(
                principal_id=principal, evidence_source="cli-argument"
            ).principal_id,
            client_id=_GATEWAY_CLIENT_ID,
        ),
    )
    return client.submit(command, credential_id=credential_id)


def _run_error(parsed: argparse.Namespace) -> int:
    workspace_root = resolve_workspace(parsed.workspace).root
    paths = PathLayout.for_workspace(workspace_root)
    read_only = (
        parsed.error_action == "review" and parsed.review_action == "list"
    )
    guard_code = _guard_implicit_workspace_init(
        parsed, paths, creating=not read_only
    )
    if guard_code is not None:
        return guard_code
    context = RuntimeContext(
        workspace_home_id=_detect_workspace_home_id(
            paths.database_path, workspace_root.name
        ),
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )
    try:
        runtime_factory = create_read_runtime if read_only else create_runtime
        with runtime_factory(context, adapter_peer_kind="fake") as runtime:
            if parsed.error_action == "report":
                # R4/P4b template-domain migration (ratified 2026-09-19):
                # routed through ApplicationAPI.submit() via Client, not a
                # direct OperationalErrorService call -- see
                # docs/design/peerhub-r4-p4b-converged-design-2026-09-17.md.
                outcome = _submit_via_gateway(runtime, ReportErrorCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.actor, request_kind="error-report"
                    ),
                    peer_key=parsed.peer,
                    pattern=parsed.pattern,
                    severity=parsed.severity,
                    detail=parsed.detail,
                    actor_id=parsed.actor,
                    threshold=parsed.threshold,
                ))
                if outcome.ok:
                    target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                    target = runtime.governance_broker.get_target(target_id)
                    assert target is not None
                    if parsed.json:
                        print(json.dumps(_json_safe(target.state)))
                    else:
                        print(
                            "Operational error recorded "
                            f"(peer={target.state['peer_key']}, "
                            f"pattern={target.state['pattern']}, "
                            f"count={target.state['count']})"
                        )
                    return 0
                else:
                    print(f"peerhub error: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
            elif parsed.error_action == "review":
                if parsed.review_action == "list":
                    reviews = runtime.quarantine_review_coordinator.list_pending_quarantine_reviews()
                    if getattr(parsed, "json", False):
                        print(json.dumps([_json_safe(r.state) for r in reviews]))
                    else:
                        for r in reviews:
                            print(
                                f"{r.state.get('review_id')}\t"
                                f"{r.state.get('peer_key')}\t"
                                f"{r.state.get('pattern')}"
                            )
                    return 0
                elif parsed.review_action == "resolve":
                    from peerhub.core.identity import AuthenticatedSubject
                    actor = AuthenticatedSubject(
                        principal_id=parsed.actor,
                        evidence_source="cli-argument",
                    )
                    submission = runtime.quarantine_review_coordinator.resolve_quarantine_review(
                        parsed.review_id,
                        decision=parsed.decision,
                        actor=actor,
                        reason=parsed.reason,
                    )
                    target = runtime.governance_broker.get_target(
                        submission.receipt.target_id
                    )
                    assert target is not None
                    if parsed.json:
                        print(json.dumps(_json_safe(target.state)))
                    else:
                        print(
                            "Quarantine review resolved "
                            f"(id={target.state['review_id']}, "
                            f"status={target.state['status']})"
                        )
                    return 0
    except (InvalidMutationError, RecordNotFoundError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"peerhub error: {exc}", file=sys.stderr)
        return 2

    return 1


def _run_alert(parsed: argparse.Namespace) -> int:
    workspace_root = resolve_workspace(parsed.workspace).root
    paths = PathLayout.for_workspace(workspace_root)
    guard_code = _guard_implicit_workspace_init(parsed, paths, creating=True)
    if guard_code is not None:
        return guard_code
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
            result = runtime.alert_raise_coordinator.raise_alert(
                room_id=parsed.room_id,
                raiser_instance_id=parsed.raiser_instance_id,
                raiser_profile_id=parsed.raiser_profile_id,
                severity=parsed.severity,
                message=parsed.message,
            )
            payload = {
                "alert_id": result.alert_id,
                "alert_target_id": result.alert_target_id,
                "room_id": result.room_id,
                "recipient_profile_ids": result.recipient_profile_ids,
                "inbox_message_target_ids": (
                    result.inbox_message_target_ids
                ),
            }
            if parsed.json:
                print(json.dumps(_json_safe(payload)))
            else:
                print(
                    f"[HUB] !!! {parsed.severity.upper()} ALERT RAISED by "
                    f"{parsed.raiser_profile_id} !!!: {parsed.message}"
                )
            return 0
    except (InvalidMutationError, RecordNotFoundError, ValueError) as exc:
        print(f"peerhub alert: {exc}", file=sys.stderr)
        return 2


def _run_room(parsed: argparse.Namespace) -> int:
    workspace_root = resolve_workspace(parsed.workspace).root
    paths = PathLayout.for_workspace(workspace_root)
    read_only = parsed.room_action in ("check-inbox", "context-fill", "status")
    guard_code = _guard_implicit_workspace_init(
        parsed, paths, creating=not read_only
    )
    if guard_code == 0:
        if parsed.json:
            print(json.dumps({"error": "not_found", "room_id": parsed.room_id}))
        else:
            print(f"Room {parsed.room_id}: not found (workspace uninitialized)")
    if guard_code is not None:
        return guard_code
    context = RuntimeContext(workspace_home_id=_detect_workspace_home_id(paths.database_path, workspace_root.name), paths=paths, clock=SystemClock(), ids=UuidSource())
    try:
        runtime_factory = create_read_runtime if read_only else create_runtime
        with runtime_factory(context, adapter_peer_kind="fake") as runtime:
            service = RoomsService(runtime.governance_broker, clock=context.clock, ids=context.ids)
            action = parsed.room_action
            if action == "create":
                outcome = _submit_via_gateway(runtime, CreateRoomCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.creator, request_kind="room-create"
                    ),
                    room_id=parsed.room_id,
                    topic_id=parsed.topic_id,
                    title=parsed.title,
                    creator_id=parsed.creator,
                    participants=tuple(
                        participant
                        for participant in parsed.participants.split(",")
                        if participant
                    ),
                ))
                if not outcome.ok:
                    print(f"peerhub room: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            elif action == "thread-new":
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client.
                outcome = _submit_via_gateway(runtime, ThreadNewCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.creator or "cc", request_kind="room-thread-new"
                    ),
                    thread_id=legacy_thread_slug(parsed.topic),
                    room_id=parsed.room_id,
                    subject=parsed.topic,
                    creator_id=parsed.creator or "cc",
                ))
                if not outcome.ok:
                    print(f"peerhub room: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                result_payload = cast(Mapping[str, JsonValue], outcome.result)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                if parsed.json:
                    print(json.dumps(_json_safe({
                        "thread_id": result_payload["thread_id"],
                        "created": result_payload["created"],
                        "message": result_payload["message"],
                    })))
                elif result_payload["message"] is not None:
                    print(f"[HUB] {result_payload['message']}")
                else:
                    print(
                        f"[HUB] THREAD-NEW '{result_payload['thread_id']}' "
                        f"| from={parsed.creator} | file={result_payload['thread_id']}.jsonl"
                    )
                return 0
            elif action == "create-thread":
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client.
                outcome = _submit_via_gateway(runtime, NewTopicCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.creator, request_kind="room-create-thread"
                    ),
                    thread_id=parsed.thread_id,
                    room_id=parsed.room_id,
                    subject=parsed.subject,
                    creator_id=parsed.creator,
                ))
                if not outcome.ok:
                    print(f"peerhub room: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            elif action == "append-message":
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client.
                outcome = _submit_via_gateway(runtime, ThreadAppendCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.author, request_kind="room-append-message"
                    ),
                    message_id=parsed.message_id,
                    room_id=parsed.room_id,
                    thread_id=parsed.thread_id,
                    author_id=parsed.author,
                    body=parsed.body,
                ))
                if not outcome.ok:
                    print(f"peerhub room: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            elif action == "send":
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client.
                outcome = _submit_via_gateway(runtime, MessageSendCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.sender_profile_id, request_kind="room-send"
                    ),
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
                ))
                if not outcome.ok:
                    print(f"peerhub room: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            elif action == "broadcast":
                targets = (
                    None
                    if parsed.targets is None
                    else tuple(
                        target.strip()
                        for target in parsed.targets.split(",")
                        if target.strip()
                    )
                )
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client.
                outcome = _submit_via_gateway(runtime, RoomBroadcastCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.from_, request_kind="room-broadcast"
                    ),
                    room_id=parsed.room_id,
                    from_=parsed.from_,
                    msg=parsed.msg,
                    targets=targets,
                    msg_type=parsed.msg_type,
                    priority=parsed.priority,
                ))
                if not outcome.ok:
                    print(f"peerhub room: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                envelope = cast(Mapping[str, JsonValue], outcome.result)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                if parsed.json:
                    print(json.dumps(_json_safe(envelope)))
                else:
                    delivered = cast(
                        "list[Mapping[str, JsonValue]]", envelope["delivered"]
                    )
                    successes = sum(
                        item["status"] == "OK" for item in delivered
                    )
                    print(
                        f"Broadcast delivered to {successes}/"
                        f"{len(delivered)} target(s)"
                    )
                return 0
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
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client.
                outcome = _submit_via_gateway(runtime, MessageMarkReadCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.recipient_profile_id, request_kind="room-mark-read"
                    ),
                    room_id=parsed.room_id,
                    recipient_instance_id=parsed.recipient_instance_id,
                    recipient_profile_id=parsed.recipient_profile_id,
                    up_through_sequence=parsed.up_through_sequence,
                ))
                if not outcome.ok:
                    print(f"peerhub room: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            elif action == "promote-message":
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client.
                outcome = _submit_via_gateway(runtime, ThreadPromoteCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.actor, request_kind="room-promote-message"
                    ),
                    message_id=parsed.message_id,
                    room_id=parsed.room_id,
                    thread_id=parsed.thread_id,
                    actor_id=parsed.actor,
                ))
                if not outcome.ok:
                    print(f"peerhub room: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            elif action in ("react", "unreact"):
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client. Both CLI actions share
                # one registered command (ThreadReactCommand.action ADD/REMOVE),
                # matching how ConsensusService-style domains already do it.
                outcome = _submit_via_gateway(runtime, ThreadReactCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.actor_profile_id, request_kind="room-react"
                    ),
                    message_id=parsed.message_id,
                    room_id=parsed.room_id,
                    actor_instance_id=parsed.actor_instance_id,
                    actor_profile_id=parsed.actor_profile_id,
                    reaction_type=parsed.reaction_type,
                    action="ADD" if action == "react" else "REMOVE",
                ))
                if not outcome.ok:
                    print(f"peerhub room: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            elif action == "append-handoff":
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client.
                outcome = _submit_via_gateway(runtime, AppendHandoffCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.actor, request_kind="room-append-handoff"
                    ),
                    room_id=parsed.room_id,
                    section=parsed.section,
                    text=parsed.text,
                    actor_id=parsed.actor,
                ))
                if not outcome.ok:
                    print(f"peerhub room: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            elif action == "checkpoint":
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client.
                outcome = _submit_via_gateway(runtime, ContinuityCheckpointCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.actor or "peerhub", request_kind="room-checkpoint"
                    ),
                    room_id=parsed.room_id,
                    actor_id=parsed.actor or "peerhub",
                ))
                if not outcome.ok:
                    print(f"peerhub room: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                checkpoint = cast(Mapping[str, JsonValue], outcome.result)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
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
            elif action == "update-status":
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client.
                outcome = _submit_via_gateway(runtime, UpdateStatusCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.actor or "peerhub-cli", request_kind="room-update-status"
                    ),
                    room_id=parsed.room_id,
                    mission=parsed.mission,
                    blocked=parsed.blocked,
                    phase=parsed.phase,
                ))
                if not outcome.ok:
                    print(f"peerhub room: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            elif action == "clear":
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client.
                outcome = _submit_via_gateway(runtime, ClearRoomCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.actor, request_kind="room-clear"
                    ),
                    old_room_id=parsed.room_id,
                    new_room_id=parsed.new_room_id,
                    subject=parsed.subject,
                    actor_id=parsed.actor,
                ))
                if not outcome.ok:
                    print(f"peerhub room: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            elif action == "rebuild-session-bindings":
                outcome = _submit_via_gateway(
                    runtime,
                    RebuildRoomSessionBindingsCommand(
                        submission=_cli_submission(
                            context,
                            actor_id="peerhub.maintenance",
                            request_kind="room-rebuild-session-bindings",
                        ),
                        room_id=parsed.room_id,
                    ),
                )
                if not outcome.ok:
                    print(f"peerhub room: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            else:
                result = collect_room_status(service, room_id=parsed.room_id, room_sessions=runtime.room_participation_coordinator)
                if parsed.json:
                    print(json.dumps(_json_safe(result)))
                else:
                    summary = result["room_summary"]
                    summary_view: Mapping[str, JsonValue] = (
                        cast(Mapping[str, JsonValue], summary)
                        if isinstance(summary, Mapping)
                        else {}
                    )
                    thread_ids = result["thread_ids"]
                    thread_count = len(thread_ids) if isinstance(thread_ids, Sequence) else 0
                    print(
                        f"Room {parsed.room_id}: "
                        f"mission={summary_view.get('mission') or '-'}, "
                        f"blocked={summary_view.get('blocked') or '-'}, "
                        f"phase={summary_view.get('phase') or '-'}, "
                        f"unread_count={result['unread_count']}, "
                        f"thread_count={thread_count}, "
                        f"message_count={result['message_count']}"
                    )
                return 0
            target = runtime.governance_broker.get_target(target_id)
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
    except (InvalidMutationError, RecordNotFoundError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"peerhub room: {exc}", file=sys.stderr)
        return 2


def _run_duty(parsed: argparse.Namespace) -> int:
    workspace_root = resolve_workspace(parsed.workspace).root
    paths = PathLayout.for_workspace(workspace_root)
    read_only = parsed.duty_action == "status"
    guard_code = _guard_implicit_workspace_init(
        parsed, paths, creating=not read_only
    )
    if guard_code == 0:
        print(f"Terminal duty for room {parsed.room_id}: UNHELD")
    if guard_code is not None:
        return guard_code
    context = RuntimeContext(workspace_home_id=_detect_workspace_home_id(paths.database_path, workspace_root.name), paths=paths, clock=SystemClock(), ids=UuidSource())
    try:
        runtime_factory = create_read_runtime if read_only else create_runtime
        with runtime_factory(context, adapter_peer_kind="fake") as runtime:
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
    except (InvalidMutationError, RecordNotFoundError, ValueError, RuntimeError, sqlite3.Error) as exc:
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
    workspace_root = resolve_workspace(parsed.workspace).root
    paths = PathLayout.for_workspace(workspace_root)
    guard_code = _guard_implicit_workspace_init(parsed, paths, creating=True)
    if guard_code is not None:
        return guard_code
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

def _run_adapter(parsed: argparse.Namespace) -> int:  # pyright: ignore[reportUnusedFunction] -- command-module compatibility seam
    from peerhub.cli.commands.setup import run_adapter

    return run_adapter(parsed, sys.modules[__name__])


def get_cli_version() -> str:
    import importlib.metadata
    import json
    import urllib.request
    import urllib.parse
    import subprocess
    from peerhub import __version__
    try:
        dist = importlib.metadata.Distribution.from_name("peerhub")
        direct_url = dist.read_text("direct_url.json")
        if direct_url:
            data = json.loads(direct_url)
            if data.get("dir_info", {}).get("editable"):
                url = str(data.get("url", ""))
                parsed_path = str(urllib.parse.urlparse(url).path)
                path = urllib.request.url2pathname(parsed_path)
                
                git_info = "unknown"
                try:
                    res = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=path, capture_output=True, text=True)
                    if res.returncode == 0:
                        short_sha = res.stdout.strip()
                        res_dirty = subprocess.run(["git", "status", "--porcelain"], cwd=path, capture_output=True, text=True)
                        dirty = "+dirty" if res_dirty.stdout.strip() else ""
                        git_info = f"git {short_sha}{dirty}"
                except Exception:
                    pass
                
                return f"{__version__} (editable: {path}, {git_info})"
    except Exception:
        pass
    return __version__


class _LazyVersionAction(argparse.Action):
    """Like argparse's built-in 'version' action, but computes the (potentially
    subprocess-spawning) version string only when --version is actually passed,
    instead of eagerly on every CLI invocation regardless of the command run."""

    def __init__(self, option_strings: Sequence[str], dest: str, **kwargs: Any) -> None:
        kwargs.setdefault("nargs", 0)
        super().__init__(option_strings, dest, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        parser._print_message(get_cli_version() + "\n", sys.stdout)
        parser.exit()


_COMMAND_TIERS: dict[str, list[str]] = {
    "Primary commands": ["ask", "broadcast", "status", "diag"],
    "Setup & config": ["workspace", "adapter", "config", "backup"],
    "Operations (peer infrastructure)": [
        "health", "peer", "lease", "gate", "node", "routing", "leadership",
    ],
    "Governance (hub.py parity — programmatic use)": [
        "consensus", "task", "lesson", "directive", "room", "duty", "session",
        "alert", "error", "feedback", "lock", "artifact", "role", "broker", "statusline",
    ],
}


class TieredHelpFormatter(argparse.HelpFormatter):
    """Groups the top-level subcommand list into tiers for --help DISPLAY
    only (P3, ratified 2026-09-12) -- invocation paths are unchanged; this
    only changes what `peerhub --help` prints. argparse has no public API
    for this, so it necessarily reaches into _SubParsersAction/
    _choices_actions (both private); explicit list[str] annotations below
    keep the rest of the method fully typed despite that."""

    def _format_action(self, action: argparse.Action) -> str:
        # reportPrivateUsage: no public argparse API distinguishes the
        # top-level subparsers action from an ordinary one.
        if isinstance(action, argparse._SubParsersAction):  # pyright: ignore[reportPrivateUsage]
            choices_actions: list[argparse.Action] = action._choices_actions  # pyright: ignore[reportPrivateUsage]
            tiered_names = {name for names in _COMMAND_TIERS.values() for name in names}
            parts: list[str] = []

            for tier_name, cmd_names in _COMMAND_TIERS.items():
                parts.append(f"  {tier_name}:")
                if tier_name.startswith("Governance"):
                    parts.append("    (Note: consensus propose/proposal-add/proposal-vote/vote overlap)")

                for cmd_name in cmd_names:
                    c = next((c for c in choices_actions if getattr(c, "dest", "") == cmd_name), None)
                    if c is not None:
                        # use base class to format it properly with indentation
                        parts.append(super()._format_action(c).rstrip("\n"))
                parts.append("")

            # Catch any commands that aren't categorized (to avoid hiding anything)
            other_cmds = [c for c in choices_actions if getattr(c, "dest", "") not in tiered_names]
            if other_cmds:
                parts.append("  Other commands:")
                for c in other_cmds:
                    parts.append(super()._format_action(c).rstrip("\n"))
                parts.append("")

            return "\n".join(parts) + "\n"
        return super()._format_action(action)


def main(args: list[str] | None = None) -> int:
    parser = create_root_parser(
        formatter_class=TieredHelpFormatter,
        version_action=_LazyVersionAction,
        version_getter=get_cli_version,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    from peerhub.cli.commands.daily import (
        register_daily_commands,
        register_status_command,
    )
    from peerhub.cli.commands.setup import (
        register_setup_commands,
        register_workspace_command,
    )

    register_workspace_command(subparsers)
    register_status_command(subparsers)
    register_setup_commands(subparsers)
    register_daily_commands(
        subparsers,
        capability_tier_names=tuple(tier.name for tier in CapabilityTier),
    )

    health_parser = subparsers.add_parser("health", help="Manage peer health")
    health_subparsers = health_parser.add_subparsers(dest="health_action", required=True)
    revalidate_parser = health_subparsers.add_parser("revalidate", help="Trigger health revalidation for a peer")
    revalidate_parser.add_argument("--workspace", default=None, help="Path to workspace root")
    revalidate_parser.add_argument("--peer", required=True, help="Peer ID to revalidate (e.g. cc)")
    revalidate_parser.add_argument("--reason", required=True, help="Reason for revalidation")
    revalidate_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    health_check_parser = health_subparsers.add_parser("check", help="Inspect or recover peer health")
    health_check_parser.add_argument("--workspace", default=None, help="Path to workspace root")
    health_check_parser.add_argument("--peer", default=None, help="Target peer node ID (default: all)")
    health_check_parser.add_argument("--recover", action="store_true", help="Reconcile and revalidate dead circuits")
    health_check_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    health_precheck_parser = health_subparsers.add_parser("precheck", help="Fail-closed pre-flight governance gate")
    health_precheck_parser.add_argument("--workspace", default=None, help="Path to workspace root")
    health_precheck_parser.add_argument("--peer", default=None, help="Comma-separated candidate peer IDs")
    health_precheck_parser.add_argument("--needs", default=None, help="Capability requirements filter")
    health_precheck_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    health_sweep_parser = health_subparsers.add_parser("sweep", help="Evaluate dynamic staleness across all peers")
    health_sweep_parser.add_argument("--workspace", default=None, help="Path to workspace root")
    health_sweep_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    peer_parser = subparsers.add_parser("peer", help="Inspect and recover peer nodes")
    peer_subparsers = peer_parser.add_subparsers(dest="peer_action", required=True)
    peer_status_parser = peer_subparsers.add_parser("status", help="Show peer lifecycle, gate, health, and adapter versions")
    peer_status_parser.add_argument("--workspace", default=None, help="Path to workspace root")
    peer_status_parser.add_argument("--peer", default=None, help="Specific peer node ID")
    peer_status_parser.add_argument("--all", action="store_true", help="Include all registered and base nodes")
    peer_status_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    peer_quarantine_parser = peer_subparsers.add_parser("quarantine", help="Manually isolate and quarantine a peer node")
    peer_quarantine_parser.add_argument("--workspace", default=None, help="Path to workspace root")
    peer_quarantine_parser.add_argument("--peer", required=True, help="Peer node ID to quarantine")
    peer_quarantine_parser.add_argument("--reason", default="manual", help="Quarantine reason (default: manual)")
    peer_quarantine_parser.add_argument("--actor", default=None, help="Explicit operator/actor ID (default: current caller)")
    peer_quarantine_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    peer_recover_parser = peer_subparsers.add_parser("recover", help="Authorize and execute evidence-backed peer recovery")
    peer_recover_parser.add_argument("--workspace", default=None, help="Path to workspace root")
    peer_recover_parser.add_argument("--peer", required=True, help="Peer node ID to recover, or 'all'")
    peer_recover_parser.add_argument("--reason", default="manual", help="Recovery reason (default: manual)")
    peer_recover_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    lease_parser = subparsers.add_parser("lease", help="Inspect session leases")
    lease_subparsers = lease_parser.add_subparsers(dest="lease_action", required=True)
    lease_status_parser = lease_subparsers.add_parser("status", help="Show active process leases and PID liveness")
    lease_status_parser.add_argument("--workspace", default=None, help="Path to workspace root")
    lease_status_parser.add_argument("--json", action="store_true", help="Emit JSON output")
    lease_sweep_parser = lease_subparsers.add_parser(
        "sweep",
        help="Recover expired process leases and optionally reap verified root PIDs",
    )
    lease_sweep_parser.add_argument("--workspace", default=None, help="Path to workspace root")
    lease_sweep_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum expired leases to sweep (default: 100)",
    )
    lease_sweep_parser.add_argument(
        "--no-reap",
        action="store_true",
        help="Recover leases and apply backoff without killing root PIDs",
    )
    lease_sweep_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    broker_parser = subparsers.add_parser(
        "broker", help="Inspect governance effect delivery status"
    )
    broker_subparsers = broker_parser.add_subparsers(
        dest="broker_action", required=True
    )
    broker_status_parser = broker_subparsers.add_parser(
        "status", help="Show unfinished governance effect deliveries"
    )
    broker_status_parser.add_argument(
        "--workspace", default=None, help="Path to workspace root"
    )
    broker_status_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum visible deliveries, from 1 to 20 (default: 20)",
    )
    broker_status_parser.add_argument(
        "--json", action="store_true", help="Emit JSON output"
    )

    gate_parser = subparsers.add_parser("gate", help="Check dispatch gate condition for an agent")
    gate_subparsers = gate_parser.add_subparsers(dest="gate_action", required=True)
    gate_check_parser = gate_subparsers.add_parser("check", help="Check if gate is open for a named agent")
    gate_check_parser.add_argument("agent", help="Agent or peer node ID to check")
    gate_check_parser.add_argument("--workspace", default=None, help="Path to workspace root")
    gate_check_parser.add_argument("--json", action="store_true", help="Emit JSON output")

    from peerhub.cli.commands.daily import register_ask_command

    register_ask_command(
        subparsers,
        capability_tier_names=tuple(tier.name for tier in CapabilityTier),
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
        default=None,
        help="Path to workspace root",
    )

    consensus_parser = subparsers.add_parser("consensus", help="Manage consensus rounds")
    consensus_subparsers = consensus_parser.add_subparsers(dest="consensus_action", required=True)
    propose_parser = consensus_subparsers.add_parser("propose", help="Propose a new consensus round")
    propose_parser.add_argument("--workspace", default=None, help="Path to the workspace root")
    propose_parser.add_argument("--round-id", required=True, help="Consensus round identifier")
    propose_parser.add_argument("--title", required=True, help="Short proposal title")
    propose_parser.add_argument("--question", required=True, help="Question for participants")
    propose_parser.add_argument("--body", required=True, help="Proposal details")
    propose_parser.add_argument("--proposer", required=True, help="Proposer peer ID")
    propose_parser.add_argument("--required", required=True, help="Comma-separated peer IDs required for quorum (for example: cc,cx,ag)")
    propose_parser.add_argument("--eligible", required=True, help="Comma-separated eligible peer IDs")
    propose_parser.add_argument("--risk", default="normal", help="Risk tier used for quorum calculation (default: normal)")
    propose_parser.add_argument(
        "--verified-required",
        action="store_true",
        help="Require a verified D-CTX credential (--credential-id on vote) to cast a vote on this round",
    )
    propose_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    proposal_add_parser = consensus_subparsers.add_parser(
        "proposal-add",
        help="Create a health-filtered legacy-compatible proposal",
    )
    proposal_add_parser.add_argument(
        "--workspace", default=None, help="Path to the workspace root"
    )
    proposal_add_parser.add_argument("--subject", required=True)
    proposal_add_parser.add_argument(
        "--from", "--peer", dest="from_peer", default="cc"
    )
    proposal_add_parser.add_argument("--impact", default="med")
    proposal_add_parser.add_argument(
        "--rationale", "--detail", dest="rationale", default=""
    )
    proposal_add_parser.add_argument("--text", default="")
    proposal_add_parser.add_argument(
        "--verified-required",
        action="store_true",
        help="Require a verified D-CTX credential (--credential-id on proposal-vote) to vote on this proposal",
    )
    proposal_add_parser.add_argument("--json", action="store_true")
    proposal_vote_parser = consensus_subparsers.add_parser(
        "proposal-vote",
        help="Vote on a legacy-compatible proposal",
    )
    proposal_vote_parser.add_argument(
        "--workspace", default=None, help="Path to the workspace root"
    )
    proposal_vote_parser.add_argument(
        "--proposal-id", "--round-id", dest="proposal_id", required=True
    )
    proposal_vote_parser.add_argument(
        "--voter", "--peer", "--agent", dest="voter", default=None
    )
    proposal_vote_parser.add_argument(
        "--vote",
        required=True,
        choices=("agree", "disagree", "abstain", "need_more_info"),
    )
    proposal_vote_parser.add_argument("--reason", default="")
    proposal_vote_parser.add_argument(
        "--credential-id",
        default=None,
        help="D-CTX credential to present for verification (see PEERHUB_CONTEXT_FILE)",
    )
    proposal_vote_parser.add_argument("--json", action="store_true")
    list_parser = consensus_subparsers.add_parser(
        "list",
        help="List every consensus proposal, including resolved rounds",
    )
    list_parser.add_argument(
        "--workspace",
        default=None,
        help="Path to the workspace root containing consensus state",
    )
    list_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON",
    )
    arbiter_review_parser = consensus_subparsers.add_parser(
        "arbiter-review",
        help="Run final arbiter review on a consensus round",
    )
    arbiter_review_parser.add_argument(
        "--workspace",
        default=None,
        help="Path to the workspace root",
    )
    arbiter_review_parser.add_argument(
        "--round-id",
        required=True,
        help="Consensus round identifier",
    )
    arbiter_review_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON",
    )
    for action in ("vote", "status"):
        command_parser = consensus_subparsers.add_parser(action, help="Cast a vote" if action == "vote" else "Read a consensus round")
        command_parser.add_argument("--workspace", default=None, help="Path to the workspace root")
        command_parser.add_argument("--round-id", required=True, help="Consensus round identifier")
        command_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
        if action == "vote":
            command_parser.add_argument(
                "--actor",
                required=False,
                default=None,
                help="Voting peer ID (omit when presenting a valid --credential-id instead)",
            )
            command_parser.add_argument("--choice", required=True, choices=("agree", "disagree", "abstain", "need_more_info"), help="Vote choice")
            command_parser.add_argument(
                "--credential-id",
                default=None,
                help="D-CTX credential to present for verification (see PEERHUB_CONTEXT_FILE)",
            )

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
        command_parser.add_argument("--workspace", default=None, help="Path to the workspace root")
        for name, required in arguments:
            command_parser.add_argument(name, required=required, default="", help=task_arg_help[name])
        command_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    lesson_parser = subparsers.add_parser("lesson", help="Manage governance lessons")
    lesson_subparsers = lesson_parser.add_subparsers(dest="lesson_action", required=True)
    lesson_specs = {
        "propose": [("--lesson-id", True), ("--title", True), ("--rule", True), ("--category", True), ("--severity", True), ("--proposer", True), ("--affected", True), ("--scope-kind", False), ("--workspace-id", False), ("--expires-at", False)],
        "approve": [("--lesson-id", True), ("--approved-by", True), ("--authority-target-id", False)],
        "activate": [("--lesson-id", True), ("--actor", True)],
        "retire": [("--lesson-id", True), ("--actor", True), ("--reason", False)],
        "supersede": [("--lesson-id", True), ("--actor", True), ("--replacement-lesson-id", True)],
        "quarantine": [("--lesson-id", True), ("--actor", True), ("--reason", True), ("--evidence", True)],
        "broadcast": [("--lesson-id", True), ("--room-id", True), ("--sender-instance-id", True), ("--sender-profile-id", True)],
        "status": [("--lesson-id", True)],
        "sweep": [],
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
        "sweep": "Retire every active, non-sticky lesson whose expires-at has passed",
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
        "--expires-at": "Optional epoch-seconds after which `lesson sweep` retires this lesson (omit for a permanent lesson)",
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
        command_parser.add_argument("--workspace", default=None, help="Path to the workspace root")
        for name, required in arguments:
            if name == "--expires-at":
                command_parser.add_argument(name, required=required, default=None, type=int, help=lesson_arg_help[name])
                continue
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

    directive_parser = subparsers.add_parser("directive", help="Manage governance directives")
    directive_subparsers = directive_parser.add_subparsers(dest="directive_action", required=True)
    directive_specs = {
        "add": [("--directive-id", True), ("--title", True), ("--rule", True), ("--effective-date", True), ("--proposer", True), ("--category", False)],
        "migrate": [("--directive-id", True), ("--title", True), ("--rule", True), ("--digest", True), ("--consumers", False), ("--source-path", True), ("--actor", True)],
        "clear": [("--directive-id", True), ("--actor", True), ("--reason", True)],
        "list": [],
    }
    directive_subcommand_help = {
        "add": "Propose a new governance directive",
        "migrate": "Migrate an existing directive",
        "clear": "Retire an active directive",
        "list": "List all directives",
    }
    directive_arg_help = {
        "--directive-id": "Directive identifier",
        "--title": "Directive title",
        "--rule": "Directive rule text",
        "--effective-date": "Effective date",
        "--proposer": "Proposer actor ID",
        "--category": "Directive category",
        "--digest": "Digest of the directive",
        "--consumers": "JSON string of consumers",
        "--source-path": "Source path of the directive",
        "--actor": "Actor ID performing the action",
        "--reason": "Reason for retirement",
    }
    for action, arguments in directive_specs.items():
        command_parser = directive_subparsers.add_parser(action, help=directive_subcommand_help[action])
        command_parser.add_argument("--workspace", default=None, help="Path to the workspace root")
        for name, required in arguments:
            command_parser.add_argument(name, required=required, default="", help=directive_arg_help[name])
        command_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    node_parser = subparsers.add_parser("node", help="Manage the peer node registry")
    node_subparsers = node_parser.add_subparsers(dest="node_action", required=True)
    node_register_parser = node_subparsers.add_parser("register", help="Register a peer node, binding it to an existing adapter kind + profile")
    node_register_parser.add_argument("--workspace", default=None, help="Path to the workspace root")
    node_register_parser.add_argument("--node-id", required=True, help="Node identifier (must not collide with a base adapter kind or CLI alias)")
    node_register_parser.add_argument("--peer-kind", required=True, help="Adapter kind or CLI alias this node binds to (e.g. cc, cx, ag)")
    node_register_parser.add_argument("--profile-id", default=None, help="Profile ID on that adapter (auto-selected if the adapter declares exactly one)")
    node_register_parser.add_argument("--tier", type=int, default=4, help="Display-only tier value (default: 4, no authority)")
    node_register_parser.add_argument("--node-type", default="agent", help="Node type (validated free text, default: agent)")
    node_register_parser.add_argument("--actor", required=True, help="Peer ID performing this registration")
    node_register_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    node_list_parser = node_subparsers.add_parser("list", help="List all peer nodes (base adapter-registry nodes plus registered ones)")
    node_list_parser.add_argument("--workspace", default=None, help="Path to the workspace root")
    node_list_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    node_bind_parser = node_subparsers.add_parser(
        "bind-profile",
        help="Bind one node/profile pair to its configured model pin",
    )
    node_bind_parser.add_argument(
        "--workspace",
        default=None,
        help="Path to the workspace root containing profile bindings",
    )
    node_bind_parser.add_argument(
        "--node-id",
        required=True,
        help="Registered or base peer-node identifier owning the binding",
    )
    node_bind_parser.add_argument(
        "--profile-id",
        required=True,
        help="Adapter profile identifier being bound",
    )
    node_bind_parser.add_argument(
        "--model-id",
        default=None,
        help=(
            "Exact configured model identifier for this node/profile pair. "
            "Required unless --selection-mode=cli_default."
        ),
    )
    node_bind_parser.add_argument(
        "--reasoning-effort",
        default=None,
        help="Optional configured reasoning-effort value",
    )
    node_bind_parser.add_argument(
        "--selection-mode",
        default="pinned",
        choices=("pinned", "cli_default"),
        help=(
            "'pinned' (default) requires --model-id. 'cli_default' "
            "explicitly requests the underlying CLI's own default model "
            "and forbids --model-id."
        ),
    )
    node_bind_parser.add_argument(
        "--actor",
        required=True,
        help="Peer or principal updating the profile binding",
    )
    node_bind_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the persisted binding as machine-readable JSON",
    )
    node_model_status_parser = node_subparsers.add_parser(
        "model-status",
        help="Show configured model pins and current peer health",
    )
    node_model_status_parser.add_argument(
        "--workspace",
        default=None,
        help="Path to the workspace root containing model configuration",
    )
    node_model_status_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured model-status rows as JSON",
    )

    lock_parser = subparsers.add_parser(
        "lock", help="Manage durable file locks"
    )
    lock_subparsers = lock_parser.add_subparsers(
        dest="lock_action", required=True
    )
    lock_acquire_parser = lock_subparsers.add_parser(
        "acquire", help="Acquire a file lock"
    )
    lock_acquire_parser.add_argument(
        "--workspace", default=None, help="Path to the workspace root"
    )
    lock_acquire_parser.add_argument(
        "--name", required=True, help="Path or name of the file to lock"
    )
    lock_acquire_parser.add_argument(
        "--owner", required=True, help="Peer ID acquiring the lock"
    )
    lock_acquire_parser.add_argument(
        "--scope", default="file", help="Lock scope (legacy)"
    )
    lock_acquire_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    lock_release_parser = lock_subparsers.add_parser(
        "release", help="Release a file lock"
    )
    lock_release_parser.add_argument(
        "--workspace", default=None, help="Path to the workspace root"
    )
    lock_release_parser.add_argument(
        "--name", required=True, help="Path or name of the file to unlock"
    )
    lock_release_parser.add_argument(
        "--owner", help="Peer ID that currently owns the lock (optional)"
    )
    lock_release_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    lock_status_parser = lock_subparsers.add_parser(
        "status", help="List active file locks"
    )
    lock_status_parser.add_argument(
        "--workspace", default=None, help="Path to the workspace root"
    )
    lock_status_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )

    artifact_parser = subparsers.add_parser(
        "artifact", help="Manage durable named artifact records"
    )
    artifact_subparsers = artifact_parser.add_subparsers(
        dest="artifact_action", required=True
    )
    artifact_claim_parser = artifact_subparsers.add_parser(
        "claim", help="Claim a named artifact for one peer"
    )
    artifact_claim_parser.add_argument(
        "--workspace", default=None, help="Path to the workspace root"
    )
    artifact_claim_parser.add_argument(
        "--name", required=True, help="Durable artifact name"
    )
    artifact_claim_parser.add_argument(
        "--peer",
        "--agent",
        dest="peer",
        default="unknown",
        help="Peer claiming the artifact (default: unknown)",
    )
    artifact_claim_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    artifact_status_parser = artifact_subparsers.add_parser(
        "status", help="Query artifact records or register a peer draft"
    )
    artifact_status_parser.add_argument(
        "--workspace", default=None, help="Path to the workspace root"
    )
    artifact_status_parser.add_argument(
        "--name", help="Optional single artifact name"
    )
    artifact_status_parser.add_argument(
        "--peer",
        "--agent",
        dest="peer",
        help="Peer whose draft path is being registered",
    )
    artifact_status_parser.add_argument(
        "--draft-path", help="Draft path to register for the peer"
    )
    artifact_status_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    artifact_finalize_parser = artifact_subparsers.add_parser(
        "finalize", help="Finalize a claimed artifact from a real file"
    )
    artifact_finalize_parser.add_argument(
        "--workspace", default=None, help="Path to the workspace root"
    )
    artifact_finalize_parser.add_argument(
        "--name", required=True, help="Claimed artifact name"
    )
    artifact_finalize_parser.add_argument(
        "--file",
        "--file-path",
        dest="file_path",
        required=True,
        help="Existing file containing the finalized artifact",
    )
    artifact_finalize_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )

    role_parser = subparsers.add_parser(
        "role", help="Manage durable workspace role assignments"
    )
    role_subparsers = role_parser.add_subparsers(
        dest="role_action", required=True
    )
    role_assign_parser = role_subparsers.add_parser(
        "assign", help="Assign or reassign one durable role to a peer node"
    )
    role_assign_parser.add_argument(
        "--workspace", default=None, help="Path to the workspace root"
    )
    role_assign_parser.add_argument(
        "--role", required=True, help="Workspace-level role name to assign"
    )
    role_assign_parser.add_argument(
        "--peer-node-id",
        required=True,
        help="Registered or base adapter node that will own the role",
    )
    role_assign_parser.add_argument(
        "--actor", required=True, help="Peer ID performing the assignment"
    )
    role_assign_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    role_release_parser = role_subparsers.add_parser(
        "release", help="Release a durable role assignment"
    )
    role_release_parser.add_argument(
        "--workspace", default=None, help="Path to the workspace root"
    )
    role_release_parser.add_argument(
        "--role", required=True, help="Workspace-level role name to release"
    )
    role_release_parser.add_argument(
        "--actor", required=True, help="Peer ID performing the release"
    )
    role_release_parser.add_argument(
        "--peer-node-id",
        default=None,
        help="Optional current-owner assertion; mismatches are rejected",
    )
    role_release_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    role_status_parser = role_subparsers.add_parser(
        "status", help="List all currently active role assignments"
    )
    role_status_parser.add_argument(
        "--workspace", default=None, help="Path to the workspace root"
    )
    role_status_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )

    routing_parser = subparsers.add_parser(
        "routing", help="Discover candidates and elect capability-fit leaders"
    )
    routing_subparsers = routing_parser.add_subparsers(
        dest="routing_action", required=True
    )
    routing_discover_parser = routing_subparsers.add_parser(
        "discover", help="Rank configured peers for a workload need"
    )
    routing_discover_parser.add_argument(
        "--workspace", default=None, help="Path to the workspace root"
    )
    routing_discover_parser.add_argument(
        "--needs", required=True, help="Capability or workload need"
    )
    routing_discover_parser.add_argument(
        "--effort", default="mid", help="Compatibility effort: low, mid, or high"
    )
    routing_discover_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    routing_elect_parser = routing_subparsers.add_parser(
        "elect-leader", help="Audit a ranking and claim its selected leader"
    )
    routing_elect_parser.add_argument(
        "--workspace", default=None, help="Path to the workspace root"
    )
    routing_elect_parser.add_argument(
        "--needs", required=True, help="Capability or workload need"
    )
    routing_elect_parser.add_argument(
        "--effort", default="mid", help="Compatibility effort: low, mid, or high"
    )
    routing_elect_parser.add_argument(
        "--reason", default="", help="Election reason recorded in the audit"
    )
    routing_elect_parser.add_argument(
        "--actor", default="peerhub-cli", help="Actor requesting the election"
    )
    routing_elect_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    routing_import_parser = routing_subparsers.add_parser(
        "import-capabilities",
        help="One-time snapshot of legacy capability configuration",
    )
    routing_import_parser.add_argument(
        "--workspace", default=None, help="Path to the workspace root"
    )
    routing_import_parser.add_argument(
        "--protocol", help="Legacy protocol.json path"
    )
    routing_import_parser.add_argument(
        "--orchestration", help="Legacy orchestration.json path"
    )

    leadership_parser = subparsers.add_parser(
        "leadership", help="Manage the workspace-global leadership slot"
    )
    leadership_subparsers = leadership_parser.add_subparsers(
        dest="leadership_action", required=True
    )
    leadership_claim_parser = leadership_subparsers.add_parser(
        "claim", help="Claim leadership, opening a challenge window"
    )
    leadership_claim_parser.add_argument(
        "--workspace", default=None, help="Path to the workspace root"
    )
    leadership_claim_parser.add_argument(
        "--peer-node-id",
        required=True,
        help="Registered or base adapter node claiming leadership",
    )
    leadership_claim_parser.add_argument(
        "--actor", required=True, help="Peer ID performing the claim"
    )
    leadership_claim_parser.add_argument(
        "--reason", default="", help="Claim reason (default: manual_claim)"
    )
    leadership_claim_parser.add_argument(
        "--domain",
        default="",
        help="Leadership domain (defaults to the reason, then general)",
    )
    leadership_claim_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    leadership_yield_parser = leadership_subparsers.add_parser(
        "yield", help="Vacate leadership (always succeeds)"
    )
    leadership_yield_parser.add_argument(
        "--workspace", default=None, help="Path to the workspace root"
    )
    leadership_yield_parser.add_argument(
        "--peer-node-id", required=True, help="Peer yielding leadership"
    )
    leadership_yield_parser.add_argument(
        "--actor", required=True, help="Peer ID performing the yield"
    )
    leadership_yield_parser.add_argument(
        "--reason", default="", help="Yield reason (default: none)"
    )
    leadership_yield_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    leadership_status_parser = leadership_subparsers.add_parser(
        "status", help="Show the current leadership record"
    )
    leadership_status_parser.add_argument(
        "--workspace", default=None, help="Path to the workspace root"
    )
    leadership_status_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )

    feedback_parser = subparsers.add_parser(
        "feedback", help="Manage the governance feedback journal"
    )
    feedback_subparsers = feedback_parser.add_subparsers(
        dest="feedback_action", required=True
    )
    feedback_add_parser = feedback_subparsers.add_parser(
        "add", help="Append one new feedback item with a fresh GAP ID"
    )
    feedback_add_parser.add_argument(
        "--workspace", default=None, help="Path to the workspace root"
    )
    feedback_add_parser.add_argument(
        "--source-peer",
        default="unknown",
        help="Peer this feedback came from (default: unknown)",
    )
    feedback_add_parser.add_argument(
        "--category",
        default="other",
        help="Feedback category (free text, default: other)",
    )
    feedback_add_parser.add_argument(
        "--severity",
        default="medium",
        help="Feedback severity (free text, default: medium)",
    )
    feedback_add_parser.add_argument(
        "--title",
        default="unknown gap",
        help="Short feedback title (default: unknown gap)",
    )
    feedback_add_parser.add_argument(
        "--detail", default="", help="Longer feedback detail (may be empty)"
    )
    feedback_add_parser.add_argument(
        "--actor", required=True, help="Peer ID recording this feedback"
    )
    feedback_add_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    feedback_list_parser = feedback_subparsers.add_parser(
        "list", help="List every feedback item, resolved ones included"
    )
    feedback_list_parser.add_argument(
        "--workspace", default=None, help="Path to the workspace root"
    )
    feedback_list_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    feedback_resolve_parser = feedback_subparsers.add_parser(
        "resolve", help="Set one feedback item's status and refresh its timestamps"
    )
    feedback_resolve_parser.add_argument(
        "--workspace", default=None, help="Path to the workspace root"
    )
    feedback_resolve_parser.add_argument(
        "--feedback-id", required=True, help="GAP ID to resolve"
    )
    feedback_resolve_parser.add_argument(
        "--status",
        required=True,
        help="New status (validated free text, e.g. done or dismissed)",
    )
    feedback_resolve_parser.add_argument(
        "--owner",
        default=None,
        help="Optional owner; omitting it preserves the existing owner",
    )
    feedback_resolve_parser.add_argument(
        "--actor", required=True, help="Peer ID performing the resolution"
    )
    feedback_resolve_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )

    error_parser = subparsers.add_parser(
        "error", help="Record durable operational-error evidence"
    )
    error_subparsers = error_parser.add_subparsers(
        dest="error_action", required=True
    )
    error_report_parser = error_subparsers.add_parser(
        "report", help="Append one report to an operational-error series"
    )
    error_report_parser.add_argument(
        "--workspace", default=None, help="Path to the workspace root"
    )
    error_report_parser.add_argument(
        "--peer",
        default="unknown",
        help="Peer key associated with the failure (default: unknown)",
    )
    error_report_parser.add_argument(
        "--pattern",
        default="unknown",
        help="Stable failure pattern used to group reports (default: unknown)",
    )
    error_report_parser.add_argument(
        "--severity",
        default="warn",
        help="Severity recorded on this report (default: warn)",
    )
    error_report_parser.add_argument(
        "--detail", default="", help="Additional failure detail (may be empty)"
    )
    error_report_parser.add_argument(
        "--actor", required=True, help="Peer ID recording this report"
    )
    error_report_parser.add_argument(
        "--threshold",
        type=int,
        default=3,
        help="Positive report count that begins review requests (default: 3)",
    )
    error_report_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    error_review_parser = error_subparsers.add_parser(
        "review", help="Review quarantine requests"
    )
    error_review_subparsers = error_review_parser.add_subparsers(
        dest="review_action", required=True
    )

    error_review_list = error_review_subparsers.add_parser("list")
    error_review_list.add_argument("--workspace", default=None)

    error_review_resolve = error_review_subparsers.add_parser("resolve")
    error_review_resolve.add_argument("--workspace", default=None)
    error_review_resolve.add_argument("--review-id", required=True)
    error_review_resolve.add_argument(
        "--decision", required=True, choices=["DISMISS", "ESCALATE"]
    )
    error_review_resolve.add_argument("--reason", required=True)
    error_review_resolve.add_argument("--actor", required=True)
    error_review_resolve.add_argument("--json", action="store_true")

    alert_parser = subparsers.add_parser(
        "alert", help="Raise durable alerts for live room participants"
    )
    alert_subparsers = alert_parser.add_subparsers(
        dest="alert_action", required=True
    )
    alert_raise_parser = alert_subparsers.add_parser(
        "raise", help="Overwrite the room's current alert and notify peers"
    )
    alert_raise_parser.add_argument(
        "--workspace", default=None, help="Path to the workspace root"
    )
    alert_raise_parser.add_argument(
        "--room-id", required=True, help="Room whose live members receive the alert"
    )
    alert_raise_parser.add_argument(
        "--raiser-instance-id",
        required=True,
        help="Raising participant's terminal instance identifier",
    )
    alert_raise_parser.add_argument(
        "--raiser-profile-id",
        required=True,
        help="Raising participant's profile identifier",
    )
    alert_raise_parser.add_argument(
        "--severity",
        default="P1",
        help="Alert severity, P0 or P1 (default: P1)",
    )
    alert_raise_parser.add_argument(
        "--message",
        "--msg",
        dest="message",
        default="",
        help="Alert message (may be empty)",
    )
    alert_raise_parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )

    room_parser = subparsers.add_parser("room", help="Manage rooms and messages")
    room_subparsers = room_parser.add_subparsers(dest="room_action", required=True)
    room_specs = {
        "create": [("--room-id", True), ("--topic-id", True), ("--title", True), ("--creator", True), ("--participants", True)],
        "thread-new": [("--room-id", True), ("--topic", True), ("--creator", False)],
        "create-thread": [("--thread-id", True), ("--room-id", True), ("--subject", True), ("--creator", True)],
        "append-message": [("--message-id", True), ("--room-id", True), ("--thread-id", True), ("--author", True), ("--body", True)],
        "send": [("--room-id", True), ("--sender-instance-id", True), ("--sender-profile-id", True), ("--recipient-instance-id", True), ("--recipient-profile-id", True), ("--body", True), ("--message-type", False), ("--thread-ref", False), ("--resource-ref", False), ("--correlation-id", False)],
        "broadcast": [("--room-id", True), ("--from", True), ("--msg", True), ("--targets", False), ("--type", False), ("--priority", False)],
        "check-inbox": [("--room-id", True), ("--caller-instance-id", True), ("--caller-profile-id", True), ("--include-read", False)],
        "mark-read": [("--room-id", True), ("--recipient-instance-id", True), ("--recipient-profile-id", True), ("--up-through-sequence", True)],
        "promote-message": [("--message-id", True), ("--room-id", True), ("--thread-id", True), ("--actor", True)],
        "react": [("--message-id", True), ("--room-id", True), ("--actor-instance-id", True), ("--actor-profile-id", True), ("--reaction-type", True)],
        "unreact": [("--message-id", True), ("--room-id", True), ("--actor-instance-id", True), ("--actor-profile-id", True), ("--reaction-type", True)],
        "append-handoff": [("--room-id", True), ("--section", True), ("--text", True), ("--actor", True)],
        "checkpoint": [("--room-id", True), ("--actor", False)],
        "context-fill": [("--room-id", True), ("--session-id", True), ("--sections", False)],
        "update-status": [("--room-id", True), ("--mission", False), ("--blocked", False), ("--phase", False), ("--actor", False)],
        "clear": [("--room-id", True), ("--new-room-id", True), ("--subject", True), ("--actor", True)],
        "rebuild-session-bindings": [("--room-id", True)],
        "status": [("--room-id", True)],
    }
    room_subcommand_help = {
        "create": "Create a new room",
        "thread-new": "Create a legacy-compatible thread inside an existing room",
        "create-thread": "Create a new thread inside an existing room",
        "append-message": "Append a message to a thread",
        "send": "Deliver one private mailbox message to a room recipient",
        "broadcast": "Deliver one fresh mailbox message to each selected room participant",
        "check-inbox": "Read this caller's private mailbox without marking messages read",
        "mark-read": "Advance one recipient's mailbox read cursor through a delivery sequence",
        "promote-message": "Copy one mailbox message into a thread and record the promotion",
        "react": "Record this peer's active reaction to a message",
        "unreact": "Remove this peer's active reaction from a message",
        "append-handoff": "Append an immutable note to the room's continuity history",
        "checkpoint": "Generate and record the room's bounded handoff projection",
        "context-fill": "Read bounded room continuity for an LLM context window",
        "update-status": "Update the room mission, blocked reason, or phase",
        "clear": "Start a fresh room boundary; the old room is preserved untouched",
        "rebuild-session-bindings": "Rebuild the room's session-binding projection from active sessions",
        "status": "Show the current state of a room",
    }
    room_arg_help = {
        "--room-id": "Room identifier",
        "--topic-id": "Topic identifier for this room",
        "--title": "Room title",
        "--creator": "Peer ID creating this room/thread",
        "--topic": "Raw thread topic; its legacy slug becomes the thread identifier",
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
        "--from": "Sending peer identifier; used as both instance and profile identity",
        "--msg": "Plain mailbox message body",
        "--targets": "Optional comma-separated participant instance/profile identifiers",
        "--type": "Mailbox message type (default: MSG)",
        "--priority": "Optional mailbox priority",
        "--include-read": "Include messages at or below the current read cursor",
        "--up-through-sequence": "Delivery sequence through which to mark this inbox read",
        "--section": "Handoff section receiving the note",
        "--text": "Continuity note text to append",
        "--session-id": "Session identifier echoed in the context envelope",
        "--sections": "Comma-separated exact section names; omit to return all six",
        "--mission": "Optional replacement room mission",
        "--blocked": "Optional replacement blocked reason",
        "--phase": "Optional replacement room phase",
        "--actor-instance-id": "Reacting peer's terminal instance identifier",
        "--actor-profile-id": "Reacting peer's profile identifier",
        "--reaction-type": "Reaction label or emoji to add or remove (for example ACK or 👍)",
        "--new-room-id": "Identifier for the fresh room created by clear",
        "--actor": "Peer ID performing this action",
    }
    for action, arguments in room_specs.items():
        command_parser = room_subparsers.add_parser(action, help=room_subcommand_help[action])
        command_parser.add_argument("--workspace", default=None, help="Path to the workspace root")
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
            elif action == "broadcast" and name == "--type":
                command_parser.add_argument(
                    name,
                    dest="msg_type",
                    required=required,
                    default="MSG",
                    help=room_arg_help[name],
                )
            elif action == "broadcast" and name == "--from":
                command_parser.add_argument(
                    name,
                    dest="from_",
                    required=required,
                    help=room_arg_help[name],
                )
            elif action == "thread-new" and name == "--creator":
                command_parser.add_argument(
                    name,
                    required=required,
                    default="cc",
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
        command_parser.add_argument("--workspace", default=None, help="Path to the workspace root")
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
            default=None,
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

    from peerhub.cli.commands.setup import run_setup_command

    setup_result = run_setup_command(parsed, sys.modules[__name__])
    if setup_result is not None:
        return setup_result

    if parsed.command == "statusline":
        return _run_statusline(parsed)

    if parsed.command == "consensus":
        return _run_consensus(parsed)

    if parsed.command == "task":
        return _run_task(parsed)

    if parsed.command == "lesson":
        return _run_lesson(parsed)

    if parsed.command == "directive":
        return _run_directive(parsed)

    if parsed.command == "node":
        return _run_node(parsed)

    if parsed.command == "lock":
        return _run_lock(parsed)

    if parsed.command == "artifact":
        return _run_artifact(parsed)

    if parsed.command == "role":
        return _run_role(parsed)

    if parsed.command == "leadership":
        return _run_leadership(parsed)

    if parsed.command == "routing":
        return _run_routing(parsed)

    if parsed.command == "feedback":
        return _run_feedback(parsed)

    if parsed.command == "error":
        return _run_error(parsed)

    if parsed.command == "alert":
        return _run_alert(parsed)

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
        return _run_status(parsed)

    if parsed.command == "health":
        return _run_health(parsed)

    if parsed.command == "peer":
        return _run_peer(parsed)

    if parsed.command == "lease":
        return _run_lease(parsed)

    if parsed.command == "broker":
        return _run_broker(parsed)

    if parsed.command == "gate":
        return _run_gate(parsed)

    if parsed.command == "ask":
        return _run_ask(parsed)
            
    return 0


def _run_config_migrate(parsed: argparse.Namespace) -> int:  # pyright: ignore[reportUnusedFunction] -- command-module compatibility seam
    from peerhub.cli.commands.setup import run_config_migrate

    return run_config_migrate(parsed, sys.modules[__name__])


def _run_config_validate(parsed: argparse.Namespace) -> int:  # pyright: ignore[reportUnusedFunction] -- command-module compatibility seam
    from peerhub.cli.commands.setup import run_config_validate

    return run_config_validate(parsed, sys.modules[__name__])


def _run_config_init(parsed: argparse.Namespace) -> int:  # pyright: ignore[reportUnusedFunction] -- command-module compatibility seam
    from peerhub.cli.commands.setup import run_config_init

    return run_config_init(parsed, sys.modules[__name__])


def _run_backup_workspace(parsed: argparse.Namespace) -> int:  # pyright: ignore[reportUnusedFunction] -- command-module compatibility seam
    from peerhub.cli.commands.setup import run_backup_workspace

    return run_backup_workspace(parsed, sys.modules[__name__])


def _run_backup_restore(parsed: argparse.Namespace) -> int:  # pyright: ignore[reportUnusedFunction] -- command-module compatibility seam
    from peerhub.cli.commands.setup import run_backup_restore

    return run_backup_restore(parsed, sys.modules[__name__])


if __name__ == "__main__":
    sys.exit(main())
def _run_lock(parsed: argparse.Namespace) -> int:
    workspace_root = resolve_workspace(parsed.workspace).root
    paths = PathLayout.for_workspace(workspace_root)
    read_only = parsed.lock_action == "status"
    guard_code = _guard_implicit_workspace_init(
        parsed, paths, creating=not read_only
    )
    if guard_code == 0:
        print("No active file locks.")
    if guard_code is not None:
        return guard_code
    context = RuntimeContext(
        workspace_home_id=_detect_workspace_home_id(
            paths.database_path, workspace_root.name
        ),
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )
    try:
        runtime_factory = create_read_runtime if read_only else create_runtime
        with runtime_factory(context, adapter_peer_kind="fake") as runtime:
            service = runtime.file_lock_service
            if parsed.lock_action == "acquire":
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client, not a direct
                # FileLockService call.
                outcome = _submit_via_gateway(runtime, LockAcquireCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.owner, request_kind="lock-acquire"
                    ),
                    name=parsed.name,
                    owner=parsed.owner,
                    lock_scope=parsed.scope,
                ))
                if not outcome.ok:
                    print(f"peerhub lock: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                target_id = cast(str, outcome.result["target_id"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                target = runtime.governance_broker.get_target(target_id)
                assert target is not None
                if parsed.json:
                    print(json.dumps(_json_safe(target.state)))
                else:
                    print(f"File {parsed.name} locked by {parsed.owner}.")
                return 0
            if parsed.lock_action == "release":
                from peerhub.governance.file_locks import FileUnlockDisposition
                # R4/P4b migration (ratified 2026-09-19): routed through
                # ApplicationAPI.submit() via Client, not a direct
                # FileLockService call.
                outcome = _submit_via_gateway(runtime, LockReleaseCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.owner, request_kind="lock-release"
                    ),
                    name=parsed.name,
                    owner=parsed.owner,
                ))
                if not outcome.ok:
                    print(f"peerhub lock: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                result = cast(Mapping[str, JsonValue], outcome.result)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                if parsed.json:
                    print(json.dumps(_json_safe(result)))
                elif result["disposition"] == FileUnlockDisposition.NOT_LOCKED.value:
                    print(f"Warning: file {parsed.name} is not locked.")
                else:
                    print(f"File {parsed.name} unlocked.")
                return 0

            # status
            locks = service.list_active_locks()
            if parsed.json:
                payload = {
                    "items": [
                        {
                            "target_id": target.target_id,
                            "revision": target.revision,
                            "state": target.state,
                        }
                        for target in locks
                    ]
                }
                print(json.dumps(_json_safe(payload)))
                return 0
                
            if not locks:
                print("No active file locks.")
            else:
                print("name\towner\tscope\tlocked_at")
                for target in locks:
                    state = target.state
                    name = state.get("name", "")
                    owner = state.get("owner", "")
                    scope = state.get("lock_scope", "")
                    locked_at = state.get("locked_at", "")
                    print(f"{name}\t{owner}\t{scope}\t{locked_at}")
            return 0
    except (InvalidMutationError, RecordNotFoundError, ValueError, RuntimeError, PeerHubError, sqlite3.Error) as exc:
        print(f"peerhub lock: {exc}", file=sys.stderr)
        return 2


def _run_artifact(parsed: argparse.Namespace) -> int:
    workspace_root = resolve_workspace(parsed.workspace).root
    paths = PathLayout.for_workspace(workspace_root)
    read_only = parsed.artifact_action == "status" and not (
        parsed.name and parsed.peer and parsed.draft_path
    )
    guard_code = _guard_implicit_workspace_init(
        parsed, paths, creating=not read_only
    )
    if guard_code == 0:
        print("{}" if parsed.name else "No artifact metadata records found.")
    if guard_code is not None:
        return guard_code
    context = RuntimeContext(
        workspace_home_id=_detect_workspace_home_id(
            paths.database_path, workspace_root.name
        ),
        paths=paths,
        clock=SystemClock(),
        ids=UuidSource(),
    )
    try:
        runtime_factory = create_read_runtime if read_only else create_runtime
        with runtime_factory(context, adapter_peer_kind="fake") as runtime:
            # R4/P4b migration (ratified 2026-09-19): routed through
            # ApplicationAPI.submit() via Client, not a direct
            # ArtifactRecordService call.
            if parsed.artifact_action == "claim":
                outcome = _submit_via_gateway(runtime, ArtifactClaimCommand(
                    submission=_cli_submission(
                        context, actor_id=parsed.peer, request_kind="artifact-claim"
                    ),
                    name=parsed.name,
                    owner=parsed.peer,
                ))
                if not outcome.ok:
                    print(f"peerhub artifact: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                artifact_state = cast(Mapping[str, JsonValue], outcome.result["artifact"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                if parsed.json:
                    print(json.dumps(_json_safe(artifact_state)))
                else:
                    print(
                        f"[HUB] ARTIFACT-CLAIM {parsed.name} | "
                        f"owner={parsed.peer}"
                    )
                return 0

            if parsed.artifact_action == "status":
                if parsed.name and parsed.peer and parsed.draft_path:
                    is_local = runtime.artifact_record_service.is_workspace_local(
                        parsed.draft_path
                    )
                    outcome = _submit_via_gateway(runtime, ArtifactStatusCommand(
                        submission=_cli_submission(
                            context,
                            actor_id=parsed.peer,
                            request_kind="artifact-draft",
                        ),
                        name=parsed.name,
                        peer=parsed.peer,
                        draft_path=parsed.draft_path,
                    ))
                    if not outcome.ok:
                        print(f"peerhub artifact: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                        return 2
                    if not is_local:
                        print(
                            "[HUB:WARN] artifact draft path is outside "
                            f"workspace: {parsed.draft_path}",
                            file=sys.stderr,
                        )
                    artifact_state = cast(Mapping[str, JsonValue], outcome.result["artifact"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                    if parsed.json:
                        print(json.dumps(_json_safe(artifact_state)))
                    else:
                        print(
                            f"[HUB] ARTIFACT-DRAFT {parsed.name} | "
                            f"peer={parsed.peer} | path={parsed.draft_path}"
                        )
                    return 0

                outcome = _submit_via_gateway(runtime, ArtifactStatusCommand(
                    submission=_cli_submission(
                        context, actor_id=None, request_kind="artifact-status"
                    ),
                    name=parsed.name,
                ))
                if not outcome.ok:
                    print(f"peerhub artifact: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                    return 2
                if "artifact" in outcome.result:  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportOperatorIssue]
                    payload: Mapping[str, JsonValue] = cast(
                        Mapping[str, JsonValue], outcome.result["artifact"]  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                    )
                    print(
                        json.dumps(
                            _json_safe(payload),
                            indent=None if parsed.json else 2,
                        )
                    )
                    return 0

                items = cast(
                    "tuple[Mapping[str, JsonValue], ...]",
                    outcome.result["items"],  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
                )
                if parsed.json:
                    print(json.dumps(_json_safe({"items": items})))
                elif not items:
                    print("No artifact metadata records found.")
                else:
                    print("artifact\towner\tstatus\tclaimed_at")
                    for state in items:
                        print(
                            f"{state.get('artifact', '')}\t"
                            f"{state.get('owner', '')}\t"
                            f"{state.get('status', '')}\t"
                            f"{state.get('claimed_at', '')}"
                        )
                return 0

            is_local = runtime.artifact_record_service.is_workspace_local(parsed.file_path)
            outcome = _submit_via_gateway(runtime, ArtifactFinalizeCommand(
                submission=_cli_submission(
                    context, actor_id=None, request_kind="artifact-finalize"
                ),
                name=parsed.name,
                file_path=parsed.file_path,
            ))
            if not outcome.ok:
                print(f"peerhub artifact: {outcome.error.message}", file=sys.stderr)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
                return 2
            if not is_local:
                print(
                    "[HUB:WARN] artifact final path is outside workspace: "
                    f"{parsed.file_path}",
                    file=sys.stderr,
                )
            artifact_state = cast(Mapping[str, JsonValue], outcome.result["artifact"])  # pyright: ignore[reportAttributeAccessIssue, reportUnknownArgumentType, reportUnknownMemberType]
            if parsed.json:
                print(json.dumps(_json_safe(artifact_state)))
            else:
                print(
                    f"[HUB] ARTIFACT-FINALIZE {parsed.name} | "
                    f"hash={artifact_state.get('hash', '')}"
                )
            return 0
    except (InvalidMutationError, RecordNotFoundError, ValueError, RuntimeError, PeerHubError, sqlite3.Error) as exc:
        print(f"peerhub artifact: {exc}", file=sys.stderr)
        return 2
