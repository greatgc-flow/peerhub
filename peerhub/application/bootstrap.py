"""Direct-ask admission bootstrap components."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from peerhub.adapters.registry import ResolvedPeerTarget
from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import PeerHubError
from peerhub.core.protocol import ErrorCode
from peerhub.dispatch.pipe import PipeRunnerConfig, run_process
from peerhub.dispatch.process import ProcessSupervisor
from peerhub.health.contract import (
    HealthPolicy,
    HealthScopeMembershipSnapshot,
)
from peerhub.telemetry.contract import (
    ReadinessObserved,
    ReadinessMeasurement,
)
from peerhub.routing.contract import (
    ConfigurationSnapshot,
)
from peerhub.core.evidence import (
    EvidenceRef,
    EvidenceState,
    EvidenceValue,
)
from peerhub.persistence.sqlite import SqliteStateStore


class ReadinessProbeFailedError(PeerHubError):
    """Raised when the readiness probe (e.g. --version) fails or times out."""
    error_code = ErrorCode.SPAWN_FAILED
    
    def __init__(self, message: str) -> None:
        super().__init__(message)


class HealthPolicyConflictError(PeerHubError):
    """Raised when a policy ID/revision exists but differs in content."""
    error_code = ErrorCode.REVISION_CONFLICT
    
    def __init__(self, message: str) -> None:
        super().__init__(message)


@dataclass(frozen=True)
class DirectAskAdmissionConfig:
    configuration: ConfigurationSnapshot
    health_policy: HealthPolicy
    membership: HealthScopeMembershipSnapshot
    readiness: ReadinessObserved
    readiness_list: tuple[ReadinessObserved, ...] = ()


def build_broadcast_admission_config(
    targets: tuple[ResolvedPeerTarget, ...],
    *,
    clock: Clock,
    ids: IdSource,
) -> DirectAskAdmissionConfig:
    """Build unified multi-target admission config for broadcast fan-out."""
    members = []
    hasher = hashlib.sha256()
    hasher.update(b"peerhub.broadcast/v1")
    readiness_list: list[ReadinessObserved] = []
    
    for target in targets:
        members.append((target.peer_kind, target.profile.profile_id))
        hasher.update(target.peer_kind.encode("utf-8"))
        hasher.update(target.profile.profile_id.encode("utf-8"))
        
        r = produce_readiness_evidence(target, clock=clock, ids=ids)
        readiness_list.append(r)
        
    config_digest = hasher.hexdigest()
    policy = HealthPolicy(
        policy_id="peerhub.direct-ask/v1",
        revision=1,
        readiness_freshness_seconds=86400,
        recovery_backoff_seconds=(1,),
        recovery_jitter_fraction=0.0,
        readiness_observation_threshold=1,
        administrative_recovery_probe_limit=1,
    )
    configuration = ConfigurationSnapshot(revision=1, digest=config_digest)
    membership = HealthScopeMembershipSnapshot(
        configuration_revision=1,
        configuration_digest=config_digest,
        configured_members=tuple(members),
        bindings=(),
    )
    
    first_r = readiness_list[0]
    return DirectAskAdmissionConfig(
        configuration=configuration,
        health_policy=policy,
        membership=membership,
        readiness=first_r,
        readiness_list=tuple(readiness_list),
    )


def _resolve_probe_invocation(executable_path: Path) -> tuple[tuple[str, ...], Path]:
    """Resolve executable_path to direct binary/runtime invocation if it is an npm .cmd wrapper."""
    name = executable_path.name.lower()
    parent = executable_path.parent
    if name == "claude.cmd":
        real_exe = parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if not real_exe.exists():
            try:
                real_exe = executable_path.resolve().parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
            except Exception:
                pass
        if real_exe.exists():
            return (str(real_exe), "--version"), real_exe.parent

    elif name == "codex.cmd":
        codex_js = parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        if not codex_js.exists():
            try:
                codex_js = executable_path.resolve().parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
            except Exception:
                pass
        node_exe = parent.parent / "node.exe"
        if not node_exe.exists():
            node_exe = parent / "node.exe"
        if codex_js.exists() and node_exe.exists():
            return (str(node_exe), str(codex_js), "--version"), parent
        codex_exe = parent / "node_modules" / "@openai" / "codex" / "node_modules" / "@openai" / "codex-win32-x64" / "vendor" / "x86_64-pc-windows-msvc" / "bin" / "codex.exe"
        if not codex_exe.exists():
            try:
                codex_exe = executable_path.resolve().parent / "node_modules" / "@openai" / "codex" / "node_modules" / "@openai" / "codex-win32-x64" / "vendor" / "x86_64-pc-windows-msvc" / "bin" / "codex.exe"
            except Exception:
                pass
        if codex_exe.exists():
            return (str(codex_exe), "--version"), codex_exe.parent

    return (str(executable_path), "--version"), parent


def produce_readiness_evidence(
    target: ResolvedPeerTarget,
    *,
    clock: Clock,
    ids: IdSource,
) -> ReadinessObserved:
    """Run a readiness probe and produce evidence."""
    now = clock.now()
    supervisor = ProcessSupervisor()
    probe_argv, probe_cwd = _resolve_probe_invocation(target.executable_path)
    config = PipeRunnerConfig(
        argv=probe_argv,
        cwd=probe_cwd,
        process_timeout_ms=5000,
    )
    
    try:
        outcome = run_process(config, supervisor)
    except Exception as exc:
        evidence_ref_hash = hashlib.sha256(f"error:spawn_failed:{exc}".encode("utf-8")).hexdigest()
        return ReadinessObserved(
            observation_id=ids.new_id("readiness-obs"),
            instance_id=target.peer_kind,
            profile_id=target.profile.profile_id,
            evidence=EvidenceValue(
                state=EvidenceState.ERROR,
                source_tag="cli_live",
                provider_id="cli-probe",
                provider_version="1",
                observed_at=None,
                captured_at=now,
                freshness_ttl=86400,
                evidence_ref=EvidenceRef(f"sha256:{evidence_ref_hash}"),
                value=None,
            ),
        )
        
    if outcome.execution_outcome.exit_code != 0:
        evidence_ref_hash = hashlib.sha256(f"error:exit_{outcome.execution_outcome.exit_code}".encode("utf-8")).hexdigest()
        return ReadinessObserved(
            observation_id=ids.new_id("readiness-obs"),
            instance_id=target.peer_kind,
            profile_id=target.profile.profile_id,
            evidence=EvidenceValue(
                state=EvidenceState.ERROR,
                source_tag="cli_live",
                provider_id="cli-probe",
                provider_version="1",
                observed_at=None,
                captured_at=now,
                freshness_ttl=86400,
                evidence_ref=EvidenceRef(f"sha256:{evidence_ref_hash}"),
                value=None,
            ),
        )
        
    output_text = outcome.canonical_stream.decode(errors="replace").strip()
    if not output_text:
        evidence_ref_hash = hashlib.sha256(b"error:no_output").hexdigest()
        return ReadinessObserved(
            observation_id=ids.new_id("readiness-obs"),
            instance_id=target.peer_kind,
            profile_id=target.profile.profile_id,
            evidence=EvidenceValue(
                state=EvidenceState.ERROR,
                source_tag="cli_live",
                provider_id="cli-probe",
                provider_version="1",
                observed_at=None,
                captured_at=now,
                freshness_ttl=86400,
                evidence_ref=EvidenceRef(f"sha256:{evidence_ref_hash}"),
                value=None,
            ),
        )
        
    hasher = hashlib.sha256()
    hasher.update(str(target.executable_path).encode("utf-8"))
    hasher.update(output_text.encode("utf-8"))
    runtime_revision = hasher.hexdigest()
    
    return ReadinessObserved(
        observation_id=ids.new_id("readiness-obs"),
        instance_id=target.peer_kind,
        profile_id=target.profile.profile_id,
        evidence=EvidenceValue(
            state=EvidenceState.MEASURED,
            source_tag="cli_live",
            provider_id="cli-probe",
            provider_version="1",
            observed_at=now,
            captured_at=now,
            freshness_ttl=86400,
            evidence_ref=EvidenceRef(f"sha256:{runtime_revision}"),
            value=ReadinessMeasurement(
                runtime_revision=runtime_revision,
                issued_at=now,
                valid_until=now + 86400,
                integrity_verified=True,
            )
        )
    )

def build_direct_ask_admission_config(
    target: ResolvedPeerTarget,
    *,
    clock: Clock,
    ids: IdSource,
) -> DirectAskAdmissionConfig:
    """Build the direct-ask admission config, including a real readiness probe."""
    
    readiness = produce_readiness_evidence(target, clock=clock, ids=ids)
    if readiness.evidence.state is EvidenceState.ERROR:
        # direct-ask preserves the pre-existing error surfacing behavior
        raise ReadinessProbeFailedError("readiness probe failed or returned no output")
    
    policy = HealthPolicy(
        policy_id="peerhub.direct-ask/v1",
        revision=1,
        readiness_freshness_seconds=86400,
        recovery_backoff_seconds=(1,),
        recovery_jitter_fraction=0.0,
        readiness_observation_threshold=1,
        administrative_recovery_probe_limit=1,
    )
    
    hasher = hashlib.sha256()
    hasher.update(b"peerhub.direct-ask/v1")
    hasher.update(target.adapter.descriptor.adapter_id.encode("utf-8"))
    hasher.update(target.adapter.descriptor.adapter_version.encode("utf-8"))
    hasher.update(target.peer_kind.encode("utf-8"))
    hasher.update(target.profile.profile_id.encode("utf-8"))
    config_digest = hasher.hexdigest()
    
    configuration = ConfigurationSnapshot(
        revision=1,
        digest=config_digest,
    )
    
    membership = HealthScopeMembershipSnapshot(
        configuration_revision=1,
        configuration_digest=config_digest,
        configured_members=((target.peer_kind, target.profile.profile_id),),
        bindings=(),
    )
    
    return DirectAskAdmissionConfig(
        configuration=configuration,
        health_policy=policy,
        membership=membership,
        readiness=readiness,
    )





def persist_direct_ask_admission(
    store: SqliteStateStore,
    config: DirectAskAdmissionConfig,
) -> None:
    """Idempotently persist the policy and evaluate readiness."""
    
    with store.unit_of_work() as uow:
        existing = uow.get_health_policy_revision(
            config.health_policy.policy_id,
            config.health_policy.revision,
        )
        if existing is not None:
            if existing != config.health_policy:
                raise HealthPolicyConflictError(
                    f"policy {config.health_policy.policy_id} revision {config.health_policy.revision} exists with different content"
                )
        else:
            uow.add_health_policy_revision(config.health_policy)
            uow.commit()
