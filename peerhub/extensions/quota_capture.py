"""Explicit quota collection into M1 Observation; never imported by Readonly Diag.

Provider parsing/transport is owned by quota_probes; no v0 runtime is loaded.
"""
from __future__ import annotations

import math
import os
import re
import subprocess
import sys
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from threading import Lock, Thread
from typing import TYPE_CHECKING, Any, BinaryIO, Callable, Generator, Iterator, Sequence, cast

from peerhub.core.load_limits import ResourceSample, probe_timeout_factor
from peerhub.core.models import utc_now_iso
from peerhub.core.peer_kinds import ALIASES, CLI_PEER_ALIASES, QUOTA_CAPABLE_PEERS
from peerhub.core.workspace import store_scratch_dir
from peerhub.extensions.observation import ObservationStore
from peerhub.extensions.observation_model import (
    QUOTA_PROBE_LOAD_DELAY, QUOTA_PROBE_LOAD_LABEL, EvidenceState, Observation,
    ResourcePool, changed_account_plan, epoch_to_iso, quota_probe_timed_out,
)
from peerhub.extensions.quota_types import ResetCreditObserved, UsageObserved

if TYPE_CHECKING:
    from peerhub.extensions.quota_probes import AccountObserved, ProbeCancellation

VERSION_PROBE_TIMEOUT_SECONDS = 3.0
VERSION_CACHE_MAX_AGE_SECONDS = 900.0
QUOTA_LOCK_WAIT_SECONDS = 3.0
QUOTA_LOCK_POLL_SECONDS = .05
_CommandFileIdentity = tuple[str, str, int, int, int, int, int]
_CommandIdentity = tuple[_CommandFileIdentity, ...]
_VERSION_CACHE: dict[str, tuple[_CommandIdentity, float, str]] = {}
_VERSION_CACHE_LOCK = Lock()


def _try_file_lock(file: BinaryIO) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt
            file.seek(4096)  # Lock a byte beyond the PID so the owner can still be read on Windows.
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


@contextmanager
def quota_refresh_lock(db_path: str | Path, *, cancelled: Callable[[], bool] | None = None,
                       wait: bool = True) -> Generator[bool, None, None]:
    """Hold a PID lock until the refresh finishes or its process exits."""
    import psutil

    scratch = store_scratch_dir(db_path)
    scratch.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + (QUOTA_LOCK_WAIT_SECONDS if wait else 0)
    # Keep a stable file identity so two contenders cannot lock different files during dead-owner recovery.
    with (scratch / "quota-refresh.lock").open("a+b") as file:
        while not _try_file_lock(file):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or (cancelled is not None and cancelled()):
                yield False
                return
            time.sleep(min(QUOTA_LOCK_POLL_SECONDS, remaining))
        file.seek(0)
        raw = file.read(32).strip()
        owner = int(raw) if raw.isdigit() else 0
        while 0 < owner <= 0x7fffffff and psutil.pid_exists(owner):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or (cancelled is not None and cancelled()):
                yield False
                return
            time.sleep(min(QUOTA_LOCK_POLL_SECONDS, remaining))
        file.seek(0)
        file.truncate()
        file.write(str(os.getpid()).encode("ascii"))
        file.flush()
        try:
            yield True
        finally:
            file.seek(0)
            file.truncate()  # Closing releases the OS lock. An interrupted owner leaves its PID for the next caller.


class _Ids:
    def new_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"


def _account_error(kind: str, reason: str, error_type: str | None = None) -> Any:
    from peerhub.extensions.quota_probes import ACCOUNT_REFS, account_failure
    return account_failure(ACCOUNT_REFS[kind], f"account_probe:{kind}", int(time.time()), EvidenceState.ERROR, reason, error_type=error_type)


ACCOUNT_RETRY_SECONDS = 300.0  # failed probes and plan mismatches wait this long, not every monitor cycle


def _account_is_fresh(store: ObservationStore, peer: str) -> bool:
    """No probe is due for a measured reading within TTL unless a plan mismatch outlives the retry backoff.

    Failures and persistent plan mismatches are retried at most once per ACCOUNT_RETRY_SECONDS.
    """
    view = store.latest(peer, "account")
    if view is None:
        return False
    if view.stored_state == EvidenceState.MEASURED:
        quota = store.latest(peer, "quota")
        return view.state == EvidenceState.MEASURED and (
            changed_account_plan(view.observation, quota.observation if quota else None) is None
            or (view.age_seconds or 0) < ACCOUNT_RETRY_SECONDS)
    return view.age_seconds is not None and view.age_seconds < ACCOUNT_RETRY_SECONDS


class AccountCollection:
    """Account readings per peer under ONE deadline. Construction starts the probes (in parallel, once per provider kind: aliases share
    the reading; peers whose account observation is still fresh are skipped), so they overlap whatever the caller does next;
    `finish(kind)` joins one provider and returns {peer: AccountObserved} for the peers that should get a new observation. A probe that
    raises or outlives `start + budget + 1 s` becomes an ERROR reading with a fixed reason."""

    def __init__(self, store: ObservationStore, targets: Sequence[tuple[str, str]], pollers: dict[str, Callable[[], Any]], budget: float) -> None:
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

        self._threads = {kind: Thread(target=run, args=(kind,), daemon=True) for kind in self._due}
        self._end = time.monotonic() + budget + 1.0  # the pollers themselves honour `budget`
        for thread in self._threads.values():
            thread.start()

    def finish(self, kind: str) -> dict[str, AccountObserved]:
        thread = self._threads.get(kind)
        if thread is None:
            return {}
        thread.join(max(0.0, self._end - time.monotonic()))
        reading = _account_error(kind, "timeout") if thread.is_alive() else self._readings.get(kind)
        return {peer: reading for peer in self._due[kind]} if reading is not None else {}


def default_account_pollers(sys_dir: Path | None, deadline_sec: float,
                            cancellation: ProbeCancellation | None = None) -> dict[str, Callable[[], Any]]:
    """The real account probes, bounded by the refresh deadline; only the builtin default accounts are collected."""
    from peerhub.extensions.quota_probes import poll_agy_account, poll_claude_account, poll_codex_account
    options: dict[str, Any] = {"cancellation": cancellation} if cancellation is not None else {}
    return {"cc": lambda: poll_claude_account(sys_dir=sys_dir, deadline_sec=deadline_sec, **options),
            "cx": lambda: poll_codex_account(sys_dir=sys_dir, deadline_sec=deadline_sec, **options),
            "ag": lambda: poll_agy_account(sys_dir=sys_dir, deadline_sec=deadline_sec, **options)}


def _command_identity(command: list[str]) -> _CommandIdentity | None:
    files: list[_CommandFileIdentity] = []
    try:
        for arg in command:
            path = Path(arg)
            if not path.is_absolute() or path.suffix.lower() in {".cmd", ".bat"}:
                return None  # The search path or launcher can select a different file.
            real = path.resolve(strict=True)
            stat = real.stat()
            files.append((os.path.normcase(str(path)), os.path.normcase(str(real)), stat.st_dev, stat.st_ino, stat.st_size,
                          stat.st_mtime_ns, stat.st_ctime_ns))
    except (OSError, RuntimeError):
        return None
    return tuple(files)


def _probe_cli_version(kind: str, sys_dir: Path | None, *, timeout: float = VERSION_PROBE_TIMEOUT_SECONDS,
                       cancellation: ProbeCancellation | None = None) -> str | None:
    from peerhub.extensions.quota_probes import real_command, resolve_sys_dir, run_text_probe
    resolved_sys = resolve_sys_dir(sys_dir)
    peer_map = {"claude": "cc", "codex": "cx", "antigravity": "ag", "cc": "cc", "cx": "cx", "ag": "ag"}
    peer = peer_map.get(kind, kind)
    cmd = real_command(peer, resolved_sys)
    if not cmd:
        return None
    _check_cancelled(cancellation)
    identity = _command_identity(cmd)
    if identity is not None:
        with _VERSION_CACHE_LOCK:
            cached = _VERSION_CACHE.get(peer)
        if cached is not None and cached[0] == identity and time.monotonic() - cached[1] < VERSION_CACHE_MAX_AGE_SECONDS:
            return cached[2]
    try:
        proc = run_text_probe([*cmd, "--version"], timeout=timeout, cancellation=cancellation)
        if proc.returncode != 0:
            return None
        m = re.search(r"\b\d+\.\d+\.\d+(?:[-+][A-Za-z0-9._-]+)?\b", proc.stdout or proc.stderr)
    except Exception:
        return None
    if m is None or (cancellation is not None and cancellation.cancelled()):
        return None
    version = m.group(0)
    if identity is not None and identity == _command_identity(cmd):
        with _VERSION_CACHE_LOCK:
            _VERSION_CACHE[peer] = (identity, time.monotonic(), version)
    return version


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


def _poll_quota(poll: Callable[..., Any], *, retry_timeouts: bool, **options: Any) -> Sequence[UsageObserved | ResetCreditObserved]:
    if retry_timeouts:
        try:
            readings = poll(**options)
        except (TimeoutError, subprocess.TimeoutExpired):
            pass
        else:
            if not any(isinstance(r, UsageObserved) and r.evidence.state == EvidenceState.ERROR
                       and quota_probe_timed_out(r.extra.get("reason")) for r in readings or ()):
                return readings
        _check_cancelled(options.get("cancellation"))
    return poll(**options)


def _check_cancelled(cancellation: ProbeCancellation | None) -> None:
    if cancellation is not None and cancellation.cancelled():
        raise OSError("quota collection cancelled")


_QuotaReadings = Sequence[UsageObserved | ResetCreditObserved] | Exception


@dataclass(frozen=True)
class _ProviderResult:
    version: str | None
    readings: dict[int, _QuotaReadings]
    accounts: dict[str, AccountObserved]


def refresh_quota(db_path: str | Path, peers: list[str], *, sys_dir: Path | None = None,
                  deadline_sec: float = 15, pollers: dict[str, Callable[..., Any]] | None = None,
                  version_checker: Callable[[str], str | None] | None = None,
                  cx_pools: Sequence[str] | None = None, cc_pools: Sequence[str] | None = None,
                  ag_pools: Sequence[str] | None = None,
                  account_pollers: dict[str, Callable[[], Any]] | None = None,
                  retry_timeouts: bool = False, cancellation: ProbeCancellation | None = None,
                  resource_reader: Callable[[], ResourceSample] | None = None,
                  incremental: bool = False) -> dict[str, Any]:
    if not peers or not math.isfinite(deadline_sec) or deadline_sec <= 0:
        raise ValueError("provide peers and a positive probe timeout")
    if not Path(db_path).is_file():
        raise FileNotFoundError(f"database not found: {db_path}")
    kinds: list[str] = []
    for peer in peers:
        kind = ALIASES.get(peer)
        if kind is None or kind not in QUOTA_CAPABLE_PEERS:
            labels = ", ".join(f"{peer}/{CLI_PEER_ALIASES[peer]}" for peer in QUOTA_CAPABLE_PEERS)
            raise ValueError(f"quota providers: {labels}")
        kinds.append(kind)
    timeout_factor = probe_timeout_factor(resource_reader)
    deadline_sec *= timeout_factor
    injected_pollers = pollers is not None
    if pollers is None:
        from peerhub.extensions.quota_probes import poll_agy_usage, poll_claude_usage, poll_codex_usage
        pollers = {"cx": poll_codex_usage, "cc": poll_claude_usage, "ag": poll_agy_usage}

    positions: dict[str, list[int]] = {}
    for i, kind in enumerate(kinds):
        positions.setdefault(kind, []).append(i)
    parallel = len(positions) > 1
    store = ObservationStore(db_path, require_existing=True)
    owns_cancellation = parallel and cancellation is None and not injected_pollers
    with ExitStack() as cleanup:
        if owns_cancellation:
            from peerhub.extensions.quota_probes import ProbeCancellation

            cancellation = ProbeCancellation()
            cleanup.callback(cancellation.close)
        probe_options: dict[str, Any] = {"cancellation": cancellation} if cancellation is not None else {}
        if account_pollers is None:
            account_pollers = {} if injected_pollers else default_account_pollers(sys_dir, deadline_sec, **probe_options)
        accounts = AccountCollection(store, list(zip(peers, kinds)), account_pollers, deadline_sec)

        def collect(kind: str, polls: dict[str, Callable[..., Any]]) -> _ProviderResult:
            _check_cancelled(cancellation)
            timeout = VERSION_PROBE_TIMEOUT_SECONDS * timeout_factor
            if cancellation is not None and not owns_cancellation:
                timeout = min(deadline_sec, timeout)
            try:
                version = (version_checker(kind) if version_checker is not None else
                           _probe_cli_version(kind, sys_dir, timeout=timeout, cancellation=cancellation))
            except Exception:
                version = None
            readings: dict[int, _QuotaReadings] = {}
            configured = {"cx": cx_pools, "cc": cc_pools, "ag": ag_pools}[kind]
            pool_options: dict[str, Any] = {"configured_pools": configured} if configured is not None else {}
            if kind == "ag" and owns_cancellation:
                pool_options["allow_exit_grace"] = True
            for i in positions[kind]:  # Alias probes share a worker and version check.
                _check_cancelled(cancellation)
                try:
                    readings[i] = _poll_quota(polls[kind], retry_timeouts=retry_timeouts, ids=_Ids(),
                                              instance_id=peers[i], profile_id="default", deadline_sec=deadline_sec,
                                              sys_dir=sys_dir, **probe_options, **pool_options)
                except Exception as exc:
                    readings[i] = exc
            _check_cancelled(cancellation)
            return _ProviderResult(version, readings, accounts.finish(kind))

        def commit(results: dict[str, _ProviderResult]) -> dict[str, Any]:
            readings = {i: reading for result in results.values() for i, reading in result.readings.items()}
            return _commit_quota(
                store, [(peers[i], kinds[i], readings[i]) for i in sorted(readings)],
                {peer: account for result in results.values() for peer, account in result.accounts.items()},
                {kind: result.version for kind, result in results.items()}, busy=timeout_factor > 1,
                cancellation=cancellation)

        def commit_provider(kind: str, result: _ProviderResult) -> dict[str, Any]:
            try:
                return commit({kind: result})
            except Exception as exc:
                _check_cancelled(cancellation)
                return {"status": "FAILED", "warnings": [type(exc).__name__]}

        def completed(polls: dict[str, Callable[..., Any]]) -> Iterator[tuple[str, _ProviderResult]]:
            if not parallel:
                yield kinds[0], collect(kinds[0], polls)
                return
            done: Queue[tuple[str, _ProviderResult | BaseException]] = Queue()

            def worker(kind: str) -> None:
                try:
                    result = collect(kind, polls)
                except BaseException as exc:
                    done.put((kind, exc))
                else:
                    done.put((kind, result))

            # Bounded by the supported providers. Daemon workers preserve the monitor's bounded exit on interruption.
            for kind in positions:
                Thread(target=worker, args=(kind,), name=f"peerhub-quota-{kind}", daemon=True).start()
            for _ in positions:
                kind, result = done.get()
                if isinstance(result, BaseException):
                    raise result
                yield kind, result

        results: dict[str, _ProviderResult] = {}
        committed: dict[str, dict[str, Any]] = {}
        try:
            for kind, result in completed(pollers):
                if incremental:
                    committed[kind] = commit_provider(kind, result)
                else:
                    results[kind] = result
        except Exception:
            if cancellation is not None:
                cancellation.close()
            raise
        except BaseException:
            if cancellation is not None:
                cancellation.stop()
            raise
        if not incremental:
            result = commit({kind: results[kind] for kind in positions})
            if cancellation is not None:
                result["warnings"].extend(cancellation.take_warnings())
            return result
        _check_cancelled(cancellation)
        statuses = {result["status"] for result in committed.values()}
        status = "OK" if statuses == {"OK"} else "FAILED" if statuses == {"FAILED"} else "PARTIAL"
        warnings = [warning for kind in positions for warning in committed[kind].get("warnings", [])]
        if cancellation is not None:
            warnings.extend(cancellation.take_warnings())
        return {"status": status, "observations": [obs for kind in positions for obs in committed[kind].get("observations", [])],
                "warnings": warnings}


def _commit_quota(store: ObservationStore, targets: Sequence[tuple[str, str, _QuotaReadings]],
                  accounts: dict[str, AccountObserved], probed_versions: dict[str, str | None], *,
                  busy: bool, cancellation: ProbeCancellation | None) -> dict[str, Any]:
    _check_cancelled(cancellation)
    kinds = probed_versions.keys()

    observed_versions = _packaged_observed_versions()
    prior_report = _read_latest_facts_report(store)
    prior_checks: dict[str, str] = prior_report.get("checks", {}) if isinstance(prior_report, dict) else {}
    prior_check_dates: dict[str, str] = prior_report.get("check_dates", {}) if isinstance(prior_report, dict) else {}
    prior_reasons: list[str] = prior_report.get("reasons", []) if isinstance(prior_report, dict) else []
    prior_unknown_pools: list[str] = [str(p) for p in prior_report.get("unknown_pools", [])] if isinstance(prior_report, dict) else []

    ag_probed_ok = False
    ag_unknown_pools: list[str] = []
    pending_obs: list[tuple[str, str | None, str, EvidenceState, dict[str, Any], str, str, str]] = []  # last item: observation kind
    warnings: list[str] = []

    def report_load_delay(peer: str, payload: dict[str, Any]) -> None:
        payload["reason"] = QUOTA_PROBE_LOAD_DELAY
        warnings.append(f"{peer}: {QUOTA_PROBE_LOAD_DELAY}: {QUOTA_PROBE_LOAD_LABEL}")

    for peer, kind, readings in targets:
        _check_cancelled(cancellation)
        try:
            if isinstance(readings, Exception):
                raise readings
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
            details = {"condition": "probe_failed", "error_type": type(exc).__name__}
            if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)):
                if busy:
                    report_load_delay(peer, details)
                else:
                    details["reason"] = "quota_probe_timeout"
            pending_obs.append((
                peer, None, f"quota_probe:{kind}", EvidenceState.ERROR, details,
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
            elif state in (EvidenceState.ABSENT, EvidenceState.UNKNOWN):
                pool_extra: dict[str, Any] = getattr(reading, "extra", None) or {}
                scope, reason = pool_extra.get("pool_scope"), pool_extra.get("reason")
                if isinstance(scope, str) and scope and isinstance(reason, str):  # configured pool without a measurement
                    pool_ref = f"quota:{kind}:{scope}"
                    store.register_resource_pool(ResourcePool(resource_pool_id=pool_ref, provider=kind, kind="QUOTA"))
                    payload["reason"] = reason
            elif state in (EvidenceState.ERROR, EvidenceState.UNAVAILABLE):
                payload["condition"] = "provider_probe_" + state.value.lower()
                extra_data: dict[str, Any] = getattr(reading, "extra", None) or {}
                for key in ("reason", "consumed_tokens", "num_turns"):
                    if key in extra_data:
                        payload[key] = extra_data[key]
                if busy and quota_probe_timed_out(payload.get("reason")):
                    report_load_delay(peer, payload)
                elif extra_data.get("warning"):
                    warnings.append(f"{peer}: {extra_data.get('reason', 'probe_warning')}: {extra_data['warning']}")
            pending_obs.append((
                peer, pool_ref, evidence.source_tag, state, payload,
                epoch_to_iso(evidence.observed_at or evidence.captured_at),
                epoch_to_iso(evidence.captured_at), "quota",
            ))

    # Only normalized account fields reach `pending_obs`.
    for peer, account in accounts.items():
        if account.warning:
            warnings.append(f"{peer}: account_probe: {account.warning}")
        stamp = epoch_to_iso(account.observed_at)
        pending_obs.append((peer, None, account.source_tag, account.state, dict(account.payload), stamp, stamp, "account"))

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
        _check_cancelled(cancellation)
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
