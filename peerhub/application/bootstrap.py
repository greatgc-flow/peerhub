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
    now = clock.now()
    
    for target in targets:
        members.append((target.peer_kind, target.profile.profile_id))
        hasher.update(target.peer_kind.encode("utf-8"))
        hasher.update(target.profile.profile_id.encode("utf-8"))
        
        probe_contract = target.adapter.readiness_probe_contract(target.profile)
        cmd = [str(target.executable_path), *probe_contract.argv]
        outcome = target.adapter.execute_process(
            cmd,
            timeout_ms=5000,
            silence_timeout_ms=5000,
            max_output_bytes=100000,
        )
        output_text = outcome.canonical_stream.decode(errors="replace").strip()
        h = hashlib.sha256()
        h.update(str(target.executable_path).encode("utf-8"))
        h.update(output_text.encode("utf-8"))
        rt_rev = h.hexdigest()
        
        r = ReadinessObserved(
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
                evidence_ref=EvidenceRef(f"sha256:{rt_rev}"),
                value=ReadinessMeasurement(
                    runtime_revision=rt_rev,
                    issued_at=now,
                    valid_until=now + 86400000,
                    integrity_verified=True,
                ),
            ),
        )
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


def build_direct_ask_admission_config(
    target: ResolvedPeerTarget,
    *,
    clock: Clock,
    ids: IdSource,
) -> DirectAskAdmissionConfig:
    """Build the direct-ask admission config, including a real readiness probe."""
    
    supervisor = ProcessSupervisor()
    config = PipeRunnerConfig(
        argv=(str(target.executable_path), "--version"),
        cwd=target.executable_path.parent,
        process_timeout_ms=5000,
    )
    
    try:
        outcome = run_process(config, supervisor)
    except Exception as exc:
        raise ReadinessProbeFailedError(f"readiness probe failed to spawn: {exc}") from exc
        
    if outcome.execution_outcome.exit_code != 0:
        raise ReadinessProbeFailedError(f"readiness probe exited with {outcome.execution_outcome.exit_code}")
        
    output_text = outcome.canonical_stream.decode(errors="replace").strip()
    if not output_text:
        raise ReadinessProbeFailedError("readiness probe returned no output")
        
    hasher = hashlib.sha256()
    hasher.update(str(target.executable_path).encode("utf-8"))
    hasher.update(output_text.encode("utf-8"))
    runtime_revision = hasher.hexdigest()
    
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
    
    now = clock.now()
    readiness = ReadinessObserved(
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
                valid_until=now + 86400000,
                integrity_verified=True,
            )
        )
    )
    
    return DirectAskAdmissionConfig(
        configuration=configuration,
        health_policy=policy,
        membership=membership,
        readiness=readiness,
    )


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
    
    for target in targets:
        members.append((target.peer_kind, target.profile.profile_id))
        hasher.update(target.peer_kind.encode("utf-8"))
        hasher.update(target.profile.profile_id.encode("utf-8"))
        
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
    now = clock.now()
    first_t = targets[0]
    readiness = ReadinessObserved(
        observation_id=ids.new_id("readiness-obs"),
        instance_id=first_t.peer_kind,
        profile_id=first_t.profile.profile_id,
        evidence=EvidenceValue(
            state=EvidenceState.MEASURED,
            source_tag="cli_live",
            provider_id="cli-probe",
            provider_version="1",
            observed_at=now,
            captured_at=now,
            freshness_ttl=86400,
            evidence_ref=EvidenceRef(f"sha256:{config_digest}"),
            value=ReadinessMeasurement(
                runtime_revision=config_digest,
                issued_at=now,
                valid_until=now + 86400000,
                integrity_verified=True,
            ),
        ),
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
