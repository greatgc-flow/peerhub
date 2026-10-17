"""Explicit quota collection into M1 Observation; never imported by Readonly Diag.

Provider parsing/transport is owned by quota_probes; no v0 runtime is loaded.
"""
from __future__ import annotations

import uuid
import math
from pathlib import Path
from typing import Any, Callable, cast

from peerhub.extensions.peer_kinds import ALIASES
from peerhub.extensions.observation import ObservationStore
from peerhub.extensions.observation_model import EvidenceState, Observation, ResourcePool, epoch_to_iso
from peerhub.extensions.quota_types import ResetCreditObserved
from peerhub.core.models import utc_now_iso


class _Ids:
    def new_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"


def _probe_cli_version(kind: str, sys_dir: Path | None) -> str | None:
    import re
    import subprocess
    from peerhub.extensions.quota_probes import real_command, resolve_sys_dir
    resolved_sys = resolve_sys_dir(sys_dir)
    peer_map = {"claude": "cc", "codex": "cx", "antigravity": "ag", "cc": "cc", "cx": "cx", "ag": "ag"}
    peer = peer_map.get(kind, kind)
    cmd = real_command(peer, resolved_sys)
    if not cmd:
        return None
    try:
        proc = subprocess.run([*cmd, "--version"], capture_output=True, text=True, timeout=3.0, check=False)
        if proc.returncode != 0:
            return None
        m = re.search(r"\b\d+\.\d+\.\d+(?:[-+][A-Za-z0-9._-]+)?\b", proc.stdout or proc.stderr)
        return m.group(0) if m else None
    except Exception:
        return None


def _read_latest_facts_report(db_path: str | Path) -> dict[str, Any] | None:
    import sqlite3
    import json
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT payload_json FROM observations "
                "WHERE kind='quota' AND payload_json LIKE '%facts_report%' "
                "ORDER BY capture_seq DESC LIMIT 1"
            ).fetchone()
            if row and row[0]:
                data_obj: object = json.loads(row[0])
                if isinstance(data_obj, dict):
                    rep_obj: object = cast(dict[str, Any], data_obj).get("facts_report")
                    if isinstance(rep_obj, dict):
                        return cast(dict[str, Any], rep_obj)
    except (sqlite3.Error, ValueError, TypeError):
        pass
    return None


def refresh_quota(db_path: str | Path, peers: list[str], *, sys_dir: Path | None = None,
                  deadline_sec: float = 15, pollers: dict[str, Callable[..., Any]] | None = None,
                  version_checker: Callable[[str], str | None] | None = None) -> dict[str, Any]:
    if not peers or not math.isfinite(deadline_sec) or deadline_sec <= 0:
        raise ValueError("provide peers and a positive probe timeout")
    if not Path(db_path).is_file():
        raise FileNotFoundError(f"database not found: {db_path}")
    kinds: list[str] = []
    for peer in peers:
        kind = ALIASES.get(peer)
        if kind is None:
            raise ValueError("quota providers: cx/codex, cc/claude, ag/agy")
        kinds.append(kind)
    if pollers is None:
        from peerhub.extensions.quota_probes import poll_agy_usage, poll_claude_usage, poll_codex_usage
        pollers = {"cx": poll_codex_usage, "cc": poll_claude_usage, "ag": poll_agy_usage}

    from peerhub.extensions.adapters.profiles import packaged_defaults_meta
    meta = packaged_defaults_meta()
    observed_versions: dict[str, str] = meta.get("observed_versions") or {}

    prior_report = _read_latest_facts_report(db_path)
    prior_checks: dict[str, str] = prior_report.get("checks", {}) if isinstance(prior_report, dict) else {}
    prior_reasons: list[str] = prior_report.get("reasons", []) if isinstance(prior_report, dict) else []

    ag_probed_ok = False
    ag_unknown_pools: list[str] = []
    probed_versions: dict[str, str | None] = {}

    def _default_v_fn(k: str) -> str | None:
        return _probe_cli_version(k, sys_dir)

    v_fn: Callable[[str], str | None] = version_checker if version_checker is not None else _default_v_fn
    for peer in peers:
        try:
            probed_versions[peer] = v_fn(peer)
        except Exception:
            probed_versions[peer] = None

    store = ObservationStore(db_path, require_existing=True)
    pending_obs: list[tuple[str, str | None, str, EvidenceState, dict[str, Any], str, str, bool]] = []
    warnings: list[str] = []

    for peer, kind in zip(peers, kinds):
        try:
            readings = pollers[kind](ids=_Ids(), instance_id=peer, profile_id="default",
                                    deadline_sec=deadline_sec, sys_dir=sys_dir)
            if not readings:
                raise ValueError("probe returned no evidence")
            if peer == "ag":
                ag_probed_ok = True
                for r in readings:
                    extra_obj: object = getattr(r, "extra", None)
                    extra_dict: dict[str, Any] = cast(dict[str, Any], extra_obj) if isinstance(extra_obj, dict) else {}
                    raw_pools: object = extra_dict.get("unknown_pools")
                    if isinstance(raw_pools, list):
                        ag_unknown_pools.extend(str(p) for p in cast(list[object], raw_pools))
        except Exception as exc:
            pending_obs.append((
                peer, None, f"quota_probe:{kind}", EvidenceState.ERROR,
                {"condition": "probe_failed", "error_type": type(exc).__name__},
                utc_now_iso(), utc_now_iso(), False,
            ))
            continue
        for reading in readings:
            if isinstance(reading, ResetCreditObserved):
                pool_ref = f"credit:{kind}:reset"
                store.register_resource_pool(ResourcePool(resource_pool_id=pool_ref, provider=kind, kind="ACCOUNT"))
                rc_payload: dict[str, Any] = {"evidence_ref": reading.evidence_ref, **reading.payload}
                if reading.state == EvidenceState.ERROR:
                    rc_payload["condition"] = "reset_credits_malformed"
                pending_obs.append((
                    peer, pool_ref, reading.source_tag, reading.state, rc_payload,
                    epoch_to_iso(reading.observed_at), epoch_to_iso(reading.captured_at), True,
                ))
                continue
            evidence = reading.evidence
            state = EvidenceState(evidence.state.value)
            measurement = evidence.value
            payload: dict[str, Any] = {"evidence_ref": str(evidence.evidence_ref)}
            pool_ref = None
            if measurement is not None and state == EvidenceState.MEASURED:
                pool_ref = f"quota:{kind}:{measurement.quota_pool_scope}"
                store.register_resource_pool(ResourcePool(resource_pool_id=pool_ref, provider=kind, kind="QUOTA"))
                payload.update(remaining_fraction=measurement.remaining_fraction,
                               window_started_at=measurement.window_started_at, resets_at=measurement.resets_at)
            elif state in (EvidenceState.ERROR, EvidenceState.UNAVAILABLE):
                payload["condition"] = "provider_probe_" + state.value.lower()
                extra_data: dict[str, Any] = getattr(reading, "extra", None) or {}
                for key in ("reason", "consumed_tokens", "num_turns"):
                    if key in extra_data:
                        payload[key] = extra_data[key]
                if extra_data.get("warning"):
                    warnings.append(f"{peer}: {extra_data.get('reason', 'probe_warning')}: {extra_data['warning']}")
            pending_obs.append((
                peer, pool_ref, evidence.source_tag, state, payload,
                epoch_to_iso(evidence.observed_at or evidence.captured_at),
                epoch_to_iso(evidence.captured_at), False,
            ))

    checks: dict[str, str] = {}
    reasons: set[str] = set()

    if "ag" in peers and ag_probed_ok:
        if ag_unknown_pools:
            checks["ag_pools"] = "drift"
            reasons.add("ag: unknown_pool")
        else:
            checks["ag_pools"] = "ok"
    else:
        if prior_checks.get("ag_pools") == "drift":
            checks["ag_pools"] = "drift"
            for r in prior_reasons:
                if r == "ag: unknown_pool":
                    reasons.add(r)
        else:
            checks["ag_pools"] = prior_checks.get("ag_pools", "unknown")

    ver_targets = [("ag", "ag_version", "agy"), ("cc", "cc_version", "claude"), ("cx", "cx_version", "codex-cli")]
    for p_id, chk_key, obs_key in ver_targets:
        if p_id in peers and p_id in probed_versions and probed_versions[p_id] is not None:
            v_val = probed_versions[p_id]
            expected = observed_versions.get(p_id) or observed_versions.get(obs_key)
            if expected and v_val != expected:
                checks[chk_key] = "drift"
                reasons.add(f"{p_id}: version_drift")
            else:
                checks[chk_key] = "ok"
        else:
            if prior_checks.get(chk_key) == "drift":
                checks[chk_key] = "drift"
                for r in prior_reasons:
                    if r.startswith(f"{p_id}:"):
                        reasons.add(r)
            else:
                checks[chk_key] = prior_checks.get(chk_key, "unknown")

    report_status = "needs_recheck" if reasons else "ok"
    facts_report = {
        "status": report_status,
        "reasons": sorted(reasons),
        "checks": checks,
        "checked_at": utc_now_iso(),
    }

    written: list[dict[str, Any]] = []
    for peer_id, pool_ref, src, st, pld, obs_at, cap_at, is_reset_credit in pending_obs:
        kind_name = "reset_credit" if is_reset_credit else "quota"
        if not is_reset_credit:
            pld["facts_report"] = facts_report
        obs = Observation(
            observation_id=uuid.uuid4().hex,
            subject_ref=peer_id,
            kind=kind_name,
            resource_pool_ref=pool_ref,
            source=src,
            state=st,
            payload=pld,
            observed_at=obs_at,
            captured_at=cap_at,
        )
        store.persist(obs)
        written.append(obs.model_dump(mode="json"))

    return {"schema_version": "1.0", "status": "PARTIAL" if any(o["state"] == "ERROR" for o in written) else "OK",
            "observations": written, "warnings": warnings}
