"""Application coordinator for peer health revalidation."""

from __future__ import annotations

from dataclasses import dataclass

from peerhub.adapters.registry import resolve_peer_target
from peerhub.core.context import Clock, IdSource
from peerhub.core.errors import InvalidMutationError
from peerhub.core.identity import AuthenticatedSubject
from peerhub.core.evidence import EvidenceState
from peerhub.core.protocol import JsonValue
from peerhub.health.contract import (
    AdmissionState,
    AvailabilityState,
    PolicyScope,
    ProbeResult,
    RecoveryProbeReceipt,
)
from peerhub.health.service import HealthService
from peerhub.application.bootstrap import produce_readiness_evidence
from peerhub.application.peer_registry import PeerRegistryService


@dataclass(frozen=True)
class RevalidationResult:
    """The outcome of a health revalidation request."""
    probe_outcome: ProbeResult
    admission_state: AdmissionState
    availability_state: AvailabilityState
    circuit_closed: bool


class HealthRevalidationCoordinator:
    """Coordinate health revalidation probing and state application."""

    def __init__(
        self,
        registry: PeerRegistryService,
        health: HealthService,
        *,
        clock: Clock,
        ids: IdSource,
    ) -> None:
        self._registry = registry
        self._health = health
        self._clock = clock
        self._ids = ids

    def current_time(self) -> int:
        """The coordinator's injected clock reading, for module-level
        callers that need to resolve an omitted ``now`` without
        reaching into the private ``_clock`` attribute."""

        return self._clock.now()

    def request_revalidation(
        self,
        *,
        peer_node_id: str,
        caller: AuthenticatedSubject,
        reason: str,
        requested_at: int,
    ) -> RevalidationResult:
        """Trigger revalidation for a peer, automatically handling circuit rules."""
        
        node = self._registry.get_node(peer_node_id)
        peer_kind = str(node.state["peer_kind"])
        profile_id = str(node.state["profile_id"])

        target = resolve_peer_target(peer_kind, profile_id=profile_id)

        projection_read = self._health.read_health_projection(
            peer_kind, profile_id, evaluated_at=requested_at
        )
        adm_state = projection_read.effective_admission_state if projection_read else AdmissionState.OPEN

        grant_id = None
        attempt_id = None
        auth = None

        if adm_state == AdmissionState.QUARANTINED:
            auth = self._health.authorize_administrative_recovery(
                peer_kind,
                profile_id,
                PolicyScope.PROFILE,
                circuit_subject=profile_id,
                subject=caller,
                reason=reason,
                requested_at=requested_at,
            )
            grant_id = auth.grant.grant_id
            attempt_id = self._ids.new_id("probe-attempt")
            self._health.claim_probe(grant_id, attempt_id=attempt_id, claimed_at=requested_at)
        elif adm_state == AdmissionState.RECOVERY_REQUIRED:
            auth = self._health.authorize_recovery(
                peer_kind,
                profile_id,
                PolicyScope.PROFILE,
                subject=profile_id,
                authorized_by=caller.principal_id,
            )
            grant_id = auth.grant.grant_id
            attempt_id = self._ids.new_id("probe-attempt")
            self._health.claim_probe(grant_id, attempt_id=attempt_id, claimed_at=requested_at)
        elif adm_state == AdmissionState.COOLDOWN:
            raise InvalidMutationError("Cannot revalidate circuit while it is in COOLDOWN")

        # Run the actual probe
        readiness = produce_readiness_evidence(target, clock=self._clock, ids=self._ids)

        is_success = readiness.evidence.state is EvidenceState.MEASURED
        probe_result = ProbeResult.SUCCESS if is_success else ProbeResult.FAILURE

        circuit_closed = False
        if grant_id is not None and attempt_id is not None:
            assert auth is not None, "grant_id/attempt_id are only set alongside auth"
            if auth.circuit.receipt is None:
                raise InvalidMutationError(
                    "circuit has no receipt to report against a claimed probe"
                )
            receipt = RecoveryProbeReceipt(
                probe_receipt_id=self._ids.new_id("probe-receipt"),
                grant_id=grant_id,
                attempt_id=attempt_id,
                reported_revision=auth.circuit.revision,
                reported_receipt=auth.circuit.receipt,
                result=probe_result,
                observed_at=self._clock.now(),
                evidence_refs=(readiness.evidence.evidence_ref,),
            )
            application = self._health.apply_probe_result(
                PolicyScope.PROFILE,
                profile_id,
                receipt=receipt,
            )
            circuit_closed = application.circuit.state.name == "CIRCUIT_CLOSED"

        runtime_revision = (
            readiness.evidence.value.runtime_revision
            if readiness.evidence.value is not None
            else "unknown"
        )
        
        adapter_declares_probe_safe = getattr(
            target.adapter.descriptor, "adapter_declares_probe_safe", True
        )

        self._health.evaluate_and_persist_readiness(
            readiness,
            sealed_runtime_revision=runtime_revision,
            adapter_declares_probe_safe=adapter_declares_probe_safe,
        )

        # read effective state again
        final_read = self._health.read_health_projection(
            peer_kind, profile_id, evaluated_at=requested_at
        )
        if final_read is None:
             raise RuntimeError("Projection vanished during revalidation")

        return RevalidationResult(
            probe_outcome=probe_result,
            admission_state=final_read.effective_admission_state,
            availability_state=final_read.effective_availability_state,
            circuit_closed=circuit_closed,
        )


_LEGACY_STATUS_BY_AVAILABILITY = {
    AvailabilityState.UNKNOWN: "UNKNOWN",
    AvailabilityState.PROBING: "UNKNOWN",
    AvailabilityState.HEALTHY: "GREEN",
    AvailabilityState.DEGRADED: "YELLOW",
    AvailabilityState.UNAVAILABLE: "RED",
    AvailabilityState.STALE: "STALE",
}

_CLOSED_ADMISSION_STATES = {
    AdmissionState.COOLDOWN,
    AdmissionState.RECOVERY_REQUIRED,
    AdmissionState.QUARANTINED,
}


def collect_health_check(
    registry: PeerRegistryService,
    health: HealthService | None,
    coordinator: HealthRevalidationCoordinator | None = None,
    caller: AuthenticatedSubject | None = None,
    *,
    peer: str | None = None,
    recover: bool = False,
    now: int | None = None,
) -> dict[str, JsonValue]:
    """Inspect or recover health for one or all registered peer nodes."""

    if peer and peer != "all":
        nodes = (registry.get_node(peer),)
    else:
        nodes = registry.list_nodes()

    peer_rows: list[dict[str, JsonValue]] = []
    for node in nodes:
        node_id = str(node.state["node_id"])
        peer_kind = str(node.state["peer_kind"])
        profile_id = str(node.state["profile_id"])

        if recover:
            if coordinator is not None and caller is not None:
                try:
                    res = coordinator.request_revalidation(
                        peer_node_id=node_id,
                        caller=caller,
                        reason="health-check --recover",
                        requested_at=(
                            now
                            if now is not None
                            else coordinator.current_time()
                        ),
                    )
                    status = (
                        "RED"
                        if res.admission_state in _CLOSED_ADMISSION_STATES
                        else _LEGACY_STATUS_BY_AVAILABILITY.get(
                            res.availability_state, "UNKNOWN"
                        )
                    )
                    peer_rows.append(
                        {
                            "peer": node_id,
                            "peer_kind": peer_kind,
                            "profile_id": profile_id,
                            "status": status,
                            "admission_state": res.admission_state.value,
                            "availability_state": res.availability_state.value,
                            "probe_outcome": res.probe_outcome.value,
                            "circuit_closed": res.circuit_closed,
                            "recovered": True,
                        }
                    )
                except Exception as exc:
                    peer_rows.append(
                        {
                            "peer": node_id,
                            "peer_kind": peer_kind,
                            "profile_id": profile_id,
                            "status": "ERROR",
                            "error": str(exc),
                            "recovered": False,
                        }
                    )
            else:
                peer_rows.append(
                    {
                        "peer": node_id,
                        "peer_kind": peer_kind,
                        "profile_id": profile_id,
                        "status": "UNKNOWN",
                        "error": "Revalidation coordinator not configured",
                        "recovered": False,
                    }
                )
        else:
            health_read = (
                None
                if health is None
                else health.read_health_projection(
                    peer_kind, profile_id, evaluated_at=now
                )
            )
            if health_read is None:
                peer_rows.append(
                    {
                        "peer": node_id,
                        "peer_kind": peer_kind,
                        "profile_id": profile_id,
                        "status": "UNKNOWN",
                        "admission_state": "UNKNOWN",
                        "availability_state": "UNKNOWN",
                        "stale_at_read": False,
                        "recovered": False,
                    }
                )
            else:
                status = (
                    "RED"
                    if health_read.effective_admission_state
                    in _CLOSED_ADMISSION_STATES
                    else _LEGACY_STATUS_BY_AVAILABILITY.get(
                        health_read.effective_availability_state,
                        "UNKNOWN",
                    )
                )
                peer_rows.append(
                    {
                        "peer": node_id,
                        "peer_kind": peer_kind,
                        "profile_id": profile_id,
                        "status": status,
                        "admission_state": (
                            health_read.effective_admission_state.value
                        ),
                        "availability_state": (
                            health_read.effective_availability_state.value
                        ),
                        "stale_at_read": health_read.stale_at_read,
                        "recovered": False,
                    }
                )

    return {"peers": tuple(peer_rows)}


def execute_peer_recover(
    registry: PeerRegistryService,
    coordinator: HealthRevalidationCoordinator,
    caller: AuthenticatedSubject,
    *,
    peer_id: str,
    reason: str = "manual",
    now: int | None = None,
) -> dict[str, JsonValue]:
    """Execute evidence-backed recovery for a single peer or all peers."""

    if peer_id == "all" or not peer_id:
        nodes = registry.list_nodes()
    else:
        try:
            nodes = (registry.get_node(peer_id),)
        except Exception as exc:
            return {
                "results": (
                    {
                        "peer": peer_id,
                        "status": "ERROR",
                        "error": f"Unknown peer node: {exc}",
                    },
                ),
            }

    results: list[dict[str, JsonValue]] = []
    for node in nodes:
        node_id = str(node.state["node_id"])
        try:
            res = coordinator.request_revalidation(
                peer_node_id=node_id,
                caller=caller,
                reason=reason,
                requested_at=(
                    now if now is not None else coordinator.current_time()
                ),
            )
            results.append(
                {
                    "peer": node_id,
                    "probe_outcome": res.probe_outcome.value,
                    "admission_state": res.admission_state.value,
                    "availability_state": res.availability_state.value,
                    "circuit_closed": res.circuit_closed,
                    "status": "OK",
                }
            )
        except Exception as exc:
            results.append(
                {
                    "peer": node_id,
                    "status": "ERROR",
                    "error": str(exc),
                }
            )

    return {"results": tuple(results)}


def collect_health_precheck(
    registry: PeerRegistryService,
    health: HealthService | None,
    *,
    peers: str | None = None,
    needs: str | None = None,
    now: int | None = None,
) -> dict[str, JsonValue]:
    """Fail-closed pre-flight governance gate evaluation across candidate peers."""

    if peers:
        peer_names = tuple(p.strip() for p in peers.split(",") if p.strip())
        scope_str = peers
    else:
        peer_names = tuple(
            str(n.state["node_id"]) for n in registry.list_nodes()
        )
        scope_str = needs if needs else "all"

    peer_results: list[dict[str, JsonValue]] = []
    all_ok = True
    for name in peer_names:
        try:
            node = registry.get_node(name)
            peer_kind = str(node.state["peer_kind"])
            profile_id = str(node.state["profile_id"])
            health_read = (
                None
                if health is None
                else health.read_health_projection(
                    peer_kind, profile_id, evaluated_at=now
                )
            )
            is_backed_off = (
                False
                if health is None
                else health.is_profile_gate_backed_off(
                    profile_id,
                    evaluated_at=now if now is not None else health.current_time(),
                )
            )

            if health_read is None:
                eligible = False
                adm_str = "UNKNOWN"
                avail_str = "UNKNOWN"
            else:
                adm_str = health_read.effective_admission_state.value
                avail_str = health_read.effective_availability_state.value
                eligible = (
                    health_read.effective_admission_state is AdmissionState.OPEN
                    and not is_backed_off
                    and health_read.effective_availability_state
                    not in (
                        AvailabilityState.UNAVAILABLE,
                        AvailabilityState.DEGRADED,
                        AvailabilityState.STALE,
                    )
                    and not health_read.stale_at_read
                )

            if not eligible:
                all_ok = False

            peer_results.append(
                {
                    "peer": name,
                    "admission_state": adm_str,
                    "availability_state": avail_str,
                    "backed_off": is_backed_off,
                    "eligible": eligible,
                }
            )
        except Exception as exc:
            all_ok = False
            peer_results.append(
                {
                    "peer": name,
                    "admission_state": "UNKNOWN",
                    "availability_state": "UNKNOWN",
                    "backed_off": False,
                    "eligible": False,
                    "error": str(exc),
                }
            )

    return {
        "ok": all_ok,
        "scope": scope_str,
        "peers": tuple(peer_results),
    }


def collect_check_gate(
    registry: PeerRegistryService,
    health: HealthService | None,
    agent: str,
    *,
    now: int | None = None,
) -> dict[str, JsonValue]:
    """Evaluate dispatch gate condition for a named agent."""

    try:
        node = registry.get_node(agent)
    except Exception as exc:
        return {
            "agent": agent,
            "gate": "OFF",
            "open": False,
            "admission_state": "UNKNOWN",
            "backed_off": False,
            "reason": f"unknown peer node: {exc}",
        }
    peer_kind = str(node.state["peer_kind"])
    profile_id = str(node.state["profile_id"])
    health_read = (
        None
        if health is None
        else health.read_health_projection(
            peer_kind, profile_id, evaluated_at=now
        )
    )
    is_backed_off = (
        False
        if health is None
        else health.is_profile_gate_backed_off(
            profile_id,
            evaluated_at=now if now is not None else health.current_time(),
        )
    )
    open_gate = bool(
        health_read is not None
        and health_read.effective_admission_state is AdmissionState.OPEN
        and not is_backed_off
    )
    adm_str = (
        health_read.effective_admission_state.value
        if health_read
        else "UNKNOWN"
    )

    return {
        "agent": agent,
        "gate": "ON" if open_gate else "OFF",
        "open": open_gate,
        "admission_state": adm_str,
        "backed_off": is_backed_off,
    }


def collect_health_sweep(
    registry: PeerRegistryService,
    health: HealthService | None,
    *,
    now: int | None = None,
) -> dict[str, JsonValue]:
    """Evaluate dynamic peer staleness across all registered nodes."""

    nodes = registry.list_nodes()
    stale_peers: list[str] = []
    for node in nodes:
        node_id = str(node.state["node_id"])
        peer_kind = str(node.state["peer_kind"])
        profile_id = str(node.state["profile_id"])
        health_read = (
            None
            if health is None
            else health.read_health_projection(
                peer_kind, profile_id, evaluated_at=now
            )
        )
        if (
            health_read is None
            or health_read.stale_at_read
            or health_read.effective_availability_state is AvailabilityState.STALE
        ):
            stale_peers.append(node_id)

    return {
        "stale_count": len(stale_peers),
        "stale_peers": tuple(stale_peers),
        "total_peers": len(nodes),
    }


def execute_peer_quarantine(
    registry: PeerRegistryService,
    health: HealthService,
    *,
    peer_id: str,
    reason: str = "manual",
    actor_id: str | None = None,
    now: int | None = None,
) -> dict[str, JsonValue]:
    """Manually quarantine a peer node by opening its profile health circuit."""

    node = registry.get_node(peer_id)
    peer_kind = str(node.state["peer_kind"])
    profile_id = str(node.state["profile_id"])

    circuit = health.open_manual_quarantine(
        PolicyScope.PROFILE,
        profile_id,
        reason=reason,
        actor_id=actor_id,
        requested_at=now,
    )
    return {
        "peer": peer_id,
        "peer_kind": peer_kind,
        "profile_id": profile_id,
        "quarantined": True,
        "circuit_state": circuit.state.value,
        "admission_state": AdmissionState.QUARANTINED.value,
        "authority_class": circuit.quarantine_authority_class.value,
        "reason": reason,
        "circuit_id": circuit.circuit_id,
        "circuit_revision": circuit.revision,
    }


