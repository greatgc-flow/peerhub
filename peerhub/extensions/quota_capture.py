"""Explicit quota collection into M1 Observation; never imported by Readonly Diag.

Provider parsing/transport is owned by quota_probes; no v0 runtime is loaded.
"""
from __future__ import annotations

import uuid
import math
from pathlib import Path
from typing import Any, Callable

from peerhub.extensions.peer_kinds import ALIASES
from peerhub.extensions.observation import ObservationStore
from peerhub.extensions.observation_model import EvidenceState, Observation, ResourcePool, epoch_to_iso
from peerhub.extensions.quota_types import ResetCreditObserved
from peerhub.core.models import utc_now_iso


class _Ids:
    def new_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"


def refresh_quota(db_path: str | Path, peers: list[str], *, sys_dir: Path | None = None,
                  deadline_sec: float = 15, pollers: dict[str, Callable[..., Any]] | None = None) -> dict[str, Any]:
    if not peers or not math.isfinite(deadline_sec) or deadline_sec <= 0:
        raise ValueError("provide peers and a positive probe timeout")
    kinds: list[str] = []
    for peer in peers:
        kind = ALIASES.get(peer)
        if kind is None:
            raise ValueError("quota providers: cx/codex, cc/claude, ag/agy")
        kinds.append(kind)
    if pollers is None:
        from peerhub.extensions.quota_probes import poll_agy_usage, poll_claude_usage, poll_codex_usage
        pollers = {"cx": poll_codex_usage, "cc": poll_claude_usage, "ag": poll_agy_usage}
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    store = ObservationStore(db_path)
    written: list[dict[str, Any]] = []
    warnings: list[str] = []
    for peer, kind in zip(peers, kinds):
        try:
            readings = pollers[kind](ids=_Ids(), instance_id=peer, profile_id="default",
                                    deadline_sec=deadline_sec, sys_dir=sys_dir)
            if not readings:
                raise ValueError("probe returned no evidence")
        except Exception as exc:
            obs = Observation(observation_id=uuid.uuid4().hex, subject_ref=peer, kind="quota",
                              source=f"quota_probe:{kind}", observed_at=utc_now_iso(), captured_at=utc_now_iso(),
                              state=EvidenceState.ERROR, payload={"condition": "probe_failed", "error_type": type(exc).__name__})
            store.persist(obs)
            written.append(obs.model_dump(mode="json"))
            continue
        for reading in readings:
            if isinstance(reading, ResetCreditObserved):
                pool_ref = f"credit:{kind}:reset"
                store.register_resource_pool(ResourcePool(resource_pool_id=pool_ref, provider=kind, kind="ACCOUNT"))
                rc_payload: dict[str, Any] = {"evidence_ref": reading.evidence_ref, **reading.payload}
                if reading.state == EvidenceState.ERROR:
                    rc_payload["condition"] = "reset_credits_malformed"
                obs = Observation(observation_id=uuid.uuid4().hex, subject_ref=peer, kind="reset_credit", resource_pool_ref=pool_ref,
                                  source=reading.source_tag, state=reading.state, payload=rc_payload,
                                  observed_at=epoch_to_iso(reading.observed_at), captured_at=epoch_to_iso(reading.captured_at))
                store.persist(obs)
                written.append(obs.model_dump(mode="json"))
                continue
            evidence = reading.evidence
            state = EvidenceState(evidence.state.value)
            measurement = evidence.value
            payload: dict[str, Any] = {"evidence_ref": str(evidence.evidence_ref)}
            pool_ref = None
            if measurement is not None and state == EvidenceState.MEASURED:
                # Scope identifies the shared provider account/window; never create a pool per session.
                pool_ref = f"quota:{kind}:{measurement.quota_pool_scope}"
                store.register_resource_pool(ResourcePool(resource_pool_id=pool_ref, provider=kind, kind="QUOTA"))
                payload.update(remaining_fraction=measurement.remaining_fraction,
                               window_started_at=measurement.window_started_at, resets_at=measurement.resets_at)
            elif state in (EvidenceState.ERROR, EvidenceState.UNAVAILABLE):
                payload["condition"] = "provider_probe_" + state.value.lower()
                extra: dict[str, Any] = getattr(reading, "extra", None) or {}
                for key in ("reason", "consumed_tokens", "num_turns"):
                    if key in extra:
                        payload[key] = extra[key]
                if extra.get("warning"):
                    warnings.append(f"{peer}: {extra.get('reason', 'probe_warning')}: {extra['warning']}")
            obs = Observation(observation_id=uuid.uuid4().hex, subject_ref=peer, kind="quota", resource_pool_ref=pool_ref,
                              source=evidence.source_tag, state=state, payload=payload,
                              observed_at=epoch_to_iso(evidence.observed_at or evidence.captured_at),
                              captured_at=epoch_to_iso(evidence.captured_at))
            store.persist(obs)
            written.append(obs.model_dump(mode="json"))
    return {"schema_version": "1.0", "status": "PARTIAL" if any(o["state"] == "ERROR" for o in written) else "OK",
            "observations": written, "warnings": warnings}
