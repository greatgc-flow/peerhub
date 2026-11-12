"""Explicit quota collection into M1 Observation; never imported by Readonly Diag.

Provider parsing/transport is owned by quota_probes; no v0 runtime is loaded.
"""
from __future__ import annotations

import time
import uuid
import math
from pathlib import Path
from typing import Any, Callable, Sequence, cast

from peerhub.extensions.peer_kinds import ALIASES
from peerhub.extensions.observation import ObservationStore
from peerhub.extensions.observation_model import EvidenceState, Observation, ResourcePool, changed_account_plan, epoch_to_iso
from peerhub.extensions.quota_types import ResetCreditObserved
from peerhub.core.models import utc_now_iso


class _Ids:
    def new_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"


def _account_error(kind: str, reason: str, error_type: str | None = None) -> Any:
    from peerhub.extensions.quota_probes import ACCOUNT_REFS, account_failure
    return account_failure(ACCOUNT_REFS[kind], f"account_probe:{kind}", int(time.time()), EvidenceState.ERROR, reason, error_type=error_type)


ACCOUNT_RETRY_SECONDS = 300.0  # a failed account probe is retried after this, not on every monitor cycle


def _account_is_fresh(store: ObservationStore, peer: str) -> bool:
    """No probe is due: the latest reading is MEASURED within the account TTL, or a failure younger than the retry backoff."""
    view = store.latest(peer, "account")
    if view is None:
        return False
    if view.stored_state == EvidenceState.MEASURED:
        quota = store.latest(peer, "quota")
        return view.state == EvidenceState.MEASURED and changed_account_plan(view.observation, quota.observation if quota else None) is None
    return view.age_seconds is not None and view.age_seconds < ACCOUNT_RETRY_SECONDS


class AccountCollection:
    """Account readings per peer under ONE deadline. Construction starts the probes (in parallel, once per provider kind: aliases share
    the reading; peers whose account observation is still fresh are skipped), so they overlap whatever the caller does next;
    `finish()` joins them and returns {peer: AccountObserved} for the peers that should get a new observation. A probe that
    raises or outlives `start + budget + 1 s` becomes an ERROR reading with a fixed reason."""

    def __init__(self, store: ObservationStore, targets: Sequence[tuple[str, str]], pollers: dict[str, Callable[[], Any]], budget: float) -> None:
        import threading

        peers_by_kind: dict[str, list[str]] = {}
        for peer, kind in targets:
            if kind in pollers and peer not in peers_by_kind.setdefault(kind, []):
                peers_by_kind[kind].append(peer)
        due = {kind: [p for p in peers if not _account_is_fresh(store, p)] for kind, peers in peers_by_kind.items()}
        self._due = {kind: peers for kind, peers in due.items() if peers}
        self._readings: dict[str, Any] = {}

        def run(kind: str) -> None:
            try:
                self._readings[kind] = pollers[kind]()
            except Exception as exc:
                self._readings[kind] = _account_error(kind, "probe_exception", type(exc).__name__)

        self._threads = {kind: threading.Thread(target=run, args=(kind,), daemon=True) for kind in self._due}
        self._end = time.monotonic() + budget + 1.0  # the pollers themselves honour `budget`
        for thread in self._threads.values():
            thread.start()

    def finish(self) -> dict[str, Any]:
        timed_out = set[str]()
        for kind, thread in self._threads.items():
            thread.join(max(0.0, self._end - time.monotonic()))
            if thread.is_alive():
                timed_out.add(kind)
        out: dict[str, Any] = {}
        for kind, peers in self._due.items():
            reading = _account_error(kind, "timeout") if kind in timed_out else self._readings.get(kind)
            if reading is not None:
                out.update({peer: reading for peer in peers})
        return out


def default_account_pollers(sys_dir: Path | None, deadline_sec: float) -> dict[str, Callable[[], Any]]:
    """The real account probes, bounded by the refresh deadline; only the builtin default accounts are collected."""
    from peerhub.extensions.quota_probes import poll_agy_account, poll_claude_account, poll_codex_account
    return {"cc": lambda: poll_claude_account(sys_dir=sys_dir, deadline_sec=deadline_sec),
            "cx": lambda: poll_codex_account(sys_dir=sys_dir, deadline_sec=deadline_sec),
            "ag": lambda: poll_agy_account(sys_dir=sys_dir, deadline_sec=deadline_sec)}


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


def _packaged_observed_versions() -> dict[str, str]:
    import importlib.resources as resources
    import tomllib
    try:
        packaged = resources.files("peerhub.config_data").joinpath("model-defaults.toml")
        with packaged.open("rb") as handle:
            layer = tomllib.load(handle)
        return dict(layer.get("observed_versions") or {})
    except Exception:
        return {}


def _read_latest_facts_report(store: ObservationStore) -> dict[str, Any] | None:
    import json
    try:
        with store.read_tx() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM observations "
                "WHERE kind='quota' "
                "ORDER BY capture_seq DESC LIMIT 20"
            ).fetchall()
            for (p_str,) in rows:
                if not p_str or "facts_report" not in p_str:
                    continue
                try:
                    data_obj: object = json.loads(p_str)
                    if isinstance(data_obj, dict):
                        rep_obj: object = cast(dict[str, Any], data_obj).get("facts_report")
                        if isinstance(rep_obj, dict):
                            return cast(dict[str, Any], rep_obj)
                except (ValueError, TypeError):
                    continue
    except Exception:
        pass
    return None


def refresh_quota(db_path: str | Path, peers: list[str], *, sys_dir: Path | None = None,
                  deadline_sec: float = 15, pollers: dict[str, Callable[..., Any]] | None = None,
                  version_checker: Callable[[str], str | None] | None = None,
                  cx_pools: Sequence[str] | None = None, cc_pools: Sequence[str] | None = None,
                  ag_pools: Sequence[str] | None = None,
                  account_pollers: dict[str, Callable[[], Any]] | None = None) -> dict[str, Any]:
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
    injected_pollers = pollers is not None
    if pollers is None:
        from peerhub.extensions.quota_probes import poll_agy_usage, poll_claude_usage, poll_codex_usage
        pollers = {"cx": poll_codex_usage, "cc": poll_claude_usage, "ag": poll_agy_usage}

    store = ObservationStore(db_path, require_existing=True)

    # Account / login / plan evidence (#329): bounded no-model-turn probes, independent of the quota probes (a failure there never
    # blocks them). They start now, run alongside the serial version and quota probes below, and are joined after the quota loop, so the
    # whole refresh stays within roughly max(quota time, probe timeout). Injected quota pollers imply no real account probes.
    if account_pollers is None:
        account_pollers = {} if injected_pollers else default_account_pollers(sys_dir, deadline_sec)
    accounts_running = AccountCollection(store, list(zip(peers, kinds)), account_pollers, deadline_sec)

    observed_versions = _packaged_observed_versions()
    prior_report = _read_latest_facts_report(store)
    prior_checks: dict[str, str] = prior_report.get("checks", {}) if isinstance(prior_report, dict) else {}
    prior_check_dates: dict[str, str] = prior_report.get("check_dates", {}) if isinstance(prior_report, dict) else {}
    prior_reasons: list[str] = prior_report.get("reasons", []) if isinstance(prior_report, dict) else []
    prior_unknown_pools: list[str] = [str(p) for p in prior_report.get("unknown_pools", [])] if isinstance(prior_report, dict) else []

    ag_probed_ok = False
    ag_unknown_pools: list[str] = []
    probed_versions: dict[str, str | None] = {}

    def _default_v_fn(k: str) -> str | None:
        return _probe_cli_version(k, sys_dir)

    v_fn: Callable[[str], str | None] = version_checker if version_checker is not None else _default_v_fn
    for kind in set(kinds):
        try:
            probed_versions[kind] = v_fn(kind)
        except Exception:
            probed_versions[kind] = None
    pending_obs: list[tuple[str, str | None, str, EvidenceState, dict[str, Any], str, str, str]] = []  # last item: observation kind
    warnings: list[str] = []

    for peer, kind in zip(peers, kinds):
        try:
            injected = {"cx": cx_pools, "cc": cc_pools, "ag": ag_pools}[kind]
            readings = pollers[kind](ids=_Ids(), instance_id=peer, profile_id="default",
                                    deadline_sec=deadline_sec, sys_dir=sys_dir,
                                    **({"configured_pools": injected} if injected is not None else {}))
            if not readings:
                raise ValueError("probe returned no evidence")
            if kind == "ag":
                # "not reported by provider" ABSENT rows come from a complete report, so they do not make the probe incomplete.
                ag_probed_ok = all(
                    not isinstance(r, ResetCreditObserved)
                    and r.evidence.state == EvidenceState.MEASURED and r.evidence.value is not None
                    for r in readings
                    if not (not isinstance(r, ResetCreditObserved) and r.evidence.state == EvidenceState.ABSENT
                            and isinstance(getattr(r, "extra", None), dict) and "pool_scope" in r.extra)
                )
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
                utc_now_iso(), utc_now_iso(), "quota",
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
                    epoch_to_iso(reading.observed_at), epoch_to_iso(reading.captured_at), "reset_credit",
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
            elif state == EvidenceState.ABSENT:
                absent_extra: dict[str, Any] = getattr(reading, "extra", None) or {}
                scope, reason = absent_extra.get("pool_scope"), absent_extra.get("reason")
                if isinstance(scope, str) and scope and isinstance(reason, str):  # X1: pool the provider did not report
                    pool_ref = f"quota:{kind}:{scope}"
                    store.register_resource_pool(ResourcePool(resource_pool_id=pool_ref, provider=kind, kind="QUOTA"))
                    payload["reason"] = reason
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
                epoch_to_iso(evidence.captured_at), "quota",
            ))

    # Join the account probes started above. Only normalized fields reach `pending_obs`.
    for peer, account in accounts_running.finish().items():
        if account.warning:
            warnings.append(f"{peer}: account_probe: {account.warning}")
        stamp = epoch_to_iso(account.observed_at)
        pending_obs.append((peer, None, account.source_tag, account.state, dict(account.payload), stamp, stamp, "account"))

    import re
    today = utc_now_iso()[:10]
    checks: dict[str, str] = {}
    check_dates: dict[str, str] = {}
    reasons: set[str] = set()

    sanitized_unknown_pools: list[str] = []

    if "ag" in kinds and ag_probed_ok:
        check_dates["ag_pools"] = today
        if ag_unknown_pools:
            checks["ag_pools"] = "drift"
            reasons.add("ag: unknown_pool")
            cleaned = [re.sub(r"[^\w\-\:\.]", "", p).strip() for p in ag_unknown_pools]
            sanitized_unknown_pools = sorted(list(dict.fromkeys(c for c in cleaned if c)))[:5]
        else:
            checks["ag_pools"] = "ok"
            sanitized_unknown_pools = []
    else:
        if prior_checks.get("ag_pools") == "drift":
            checks["ag_pools"] = "drift"
            check_dates["ag_pools"] = prior_check_dates.get("ag_pools", today)
            sanitized_unknown_pools = prior_unknown_pools[:5]
            for r in prior_reasons:
                if r == "ag: unknown_pool":
                    reasons.add(r)
        else:
            checks["ag_pools"] = prior_checks.get("ag_pools", "unknown")
            sanitized_unknown_pools = []
            if "ag_pools" in prior_check_dates:
                check_dates["ag_pools"] = prior_check_dates["ag_pools"]

    ver_targets = [("ag", "ag_version", "agy"), ("cc", "cc_version", "claude"), ("cx", "cx_version", "codex-cli")]
    for p_kind, chk_key, obs_key in ver_targets:
        if p_kind in kinds and p_kind in probed_versions and probed_versions[p_kind] is not None:
            v_val = probed_versions[p_kind]
            expected = observed_versions.get(p_kind) or observed_versions.get(obs_key)
            check_dates[chk_key] = today
            if expected and v_val != expected:
                checks[chk_key] = "drift"
                reasons.add(f"{p_kind}: version_drift")
            else:
                checks[chk_key] = "ok"
        else:
            if prior_checks.get(chk_key) == "drift":
                checks[chk_key] = "drift"
                check_dates[chk_key] = prior_check_dates.get(chk_key, today)
                for r in prior_reasons:
                    if r.startswith(f"{p_kind}:"):
                        reasons.add(r)
            else:
                checks[chk_key] = prior_checks.get(chk_key, "unknown")
                if chk_key in prior_check_dates:
                    check_dates[chk_key] = prior_check_dates[chk_key]

    if reasons:
        report_status = "needs_recheck"
    elif any(v == "ok" for v in checks.values()):
        report_status = "ok"
    else:
        report_status = "unverified"

    ok_dates = [check_dates[k] for k, v in checks.items() if v == "ok" and k in check_dates]
    report_checked_at: str | None = max(ok_dates) if ok_dates else (today if report_status == "ok" else None)

    facts_report = {
        "status": report_status,
        "reasons": sorted(reasons),
        "checks": checks,
        "check_dates": check_dates,
        "unknown_pools": sanitized_unknown_pools,
        "checked_at": report_checked_at,
    }

    written: list[dict[str, Any]] = []
    for peer_id, pool_ref, src, st, pld, obs_at, cap_at, kind_name in pending_obs:
        if kind_name == "quota":
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

    return {"schema_version": "1.0", "status": "PARTIAL" if any(o["state"] == "ERROR" and o["kind"] != "account" for o in written) else "OK",  # account evidence never degrades quota status
            "observations": written, "warnings": warnings}
