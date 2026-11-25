"""Shared command resolution, probe lifecycle, parsing and evidence helpers."""
import json
import math
import os
import shutil
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta, timezone, tzinfo
from pathlib import Path
from threading import Event, Lock
from typing import Any, Optional, Sequence, TypeGuard, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import peerhub.extensions.win_job as win_job
from peerhub.extensions.binary_resolution import CLAUDE_CMD, CODEX_CMD
from peerhub.extensions.process_tree import kill_process_tree
from peerhub.extensions.quota_types import (
    NOT_REPORTED_REASON,
    EvidenceRef,
    EvidenceState,
    EvidenceValue,
    IdSource,
    UsageMeasurement,
    UsageObserved,
)


def resolve_sys_dir(sys_dir: Optional[Path] = None) -> Path:
    """Resolve the _sys directory from explicit parameter, env var, or workspace-relative default.

    No hard-coded drive letters or Engram-specific paths.
    """
    if sys_dir is not None:
        return sys_dir

    env_sys = os.environ.get("PEERHUB_SYS_DIR")
    if env_sys:
        p = Path(env_sys)
        if p.exists() and p.is_dir():
            return p

    return Path.cwd() / "_sys"


def resolve_workspace_root(sys_dir: Path) -> Path:
    """Derive workspace root from _sys dir (parent of _sys)."""
    return sys_dir.parent


def _real_binary(peer: str, sys_dir: Optional[Path] = None) -> Optional[str]:
    names = {"ag": ("agy.exe", "agy"), "cc": (CLAUDE_CMD, "claude"), "cx": (CODEX_CMD, "codex")}
    if peer not in names:
        return None
    override_key = f"PEERHUB_{peer.upper()}_BINARY"
    override = os.environ.get(override_key)
    if override is not None:
        cand = Path(override)
        if not override or not cand.is_file():
            raise FileNotFoundError(f"{override_key} points to a missing file: {override!r}")
    else:
        npm = os.environ.get("PEERHUB_NPM_GLOBAL_DIR")
        configured = Path(npm) / names[peer][0] if npm and peer in ("cc", "cx") else None
        discovered = str(configured) if configured and configured.is_file() else next(
            (found for name in names[peer] if (found := shutil.which(name))), None)
        if discovered is None:
            return None
        cand = Path(discovered)
    resolved = cand.resolve()
    cli_dir = (resolve_sys_dir(sys_dir) / "cli").resolve()
    if resolved == cli_dir or cli_dir in resolved.parents:
        raise RuntimeError(f"refusing wrapper binary for {peer}: {resolved}")
    return str(cand)  # keep literal paths: resolved junction targets may contain cmd.exe metacharacters


def real_command(peer: str, sys_dir: Optional[Path] = None) -> Optional[list[str]]:
    raw_bin = _real_binary(peer, sys_dir)
    if not raw_bin:
        return None
    cand = Path(raw_bin)
    from peerhub.extensions.binary_resolution import resolve_direct_binary
    if peer in ("cc", "cx"):
        if cand.suffix.lower() == ".cmd":
            result = resolve_direct_binary(cand)
            if result is not None:
                return result
        return [raw_bin]
    return [raw_bin]


PROBE_TREE_KILL_TIMEOUT_SECONDS = .25
PROBE_EXIT_WAIT_SECONDS = .25
_POPEN_TYPE = subprocess.Popen


class ProbeCancellation:
    """Own the subprocesses of one cancellable collection, including account probes."""

    def __init__(self) -> None:
        self._stopped = Event()
        self._lock = Lock()
        self._closed = False
        self._processes: dict[subprocess.Popen[str], win_job.Job | None] = {}
        self._warnings: list[str] = []

    def cancelled(self) -> bool:
        return self._stopped.is_set()

    def take_warnings(self) -> list[str]:
        with self._lock:
            warnings, self._warnings = self._warnings, []
            return warnings

    def spawn(self, command: Sequence[str], **options: Any) -> subprocess.Popen[str]:
        with self._lock:
            if self._closed or self.cancelled():
                raise OSError("quota collection cancelled")
            if os.name == "nt":
                try:
                    return self._spawn_windows(command, options)
                except win_job.ProbeResumeError as exc:
                    self._warnings.append(str(exc))
                    raise
            options["start_new_session"] = True
            proc: subprocess.Popen[str] = subprocess.Popen(command, **options)
            self._processes[proc] = None
            return proc

    def _spawn_windows(self, command: Sequence[str], options: dict[str, Any]) -> subprocess.Popen[str]:
        resume = win_job.resolve_resume_process()
        no_window: int = getattr(subprocess, "CREATE_NO_WINDOW")
        options["creationflags"] = options.get("creationflags", 0) | no_window | win_job.CREATE_SUSPENDED
        options["start_new_session"] = False
        proc: subprocess.Popen[str] = subprocess.Popen(command, **options)
        job = None
        try:
            # A substituted launcher can return a process double that was never suspended.
            if not isinstance(cast(object, proc), _POPEN_TYPE) and not hasattr(proc, "_handle"):
                self._processes[proc] = None
                return proc
            try:
                handle = int(getattr(proc, "_handle"))
            except (AttributeError, TypeError, ValueError, OverflowError):
                handle = 0
            if not handle:
                raise win_job.ProbeResumeError("NtResumeProcess could not access the probe process handle")
            try:
                job = win_job.Job(handle)
            except OSError as exc:
                self._warnings.append(f"Windows quota probe has no tree containment ({exc})")
            if self.cancelled():  # stop() sets the event before it takes the lock this spawn holds: never start a cancelled probe
                raise OSError("quota collection cancelled")
            resume(handle)
            self._processes[proc] = job
            return proc
        except BaseException:
            with suppress(Exception):
                proc.kill()
            if job is not None:
                job.terminate_and_close()
            with suppress(Exception):
                proc.wait(timeout=PROBE_EXIT_WAIT_SECONDS)
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                if pipe is not None:
                    with suppress(Exception):
                        pipe.close()
            raise

    @staticmethod
    def _terminate(proc: subprocess.Popen[str], job: win_job.Job | None) -> None:
        if os.name == "nt":
            with suppress(Exception):
                if job is not None:
                    job.terminate_and_close()
                elif proc.poll() is None:
                    proc.kill()
                else:
                    return
            with suppress(Exception):
                proc.wait(timeout=PROBE_EXIT_WAIT_SECONDS)
            return
        if proc.poll() is not None:
            return
        try:
            if not kill_process_tree(proc, timeout=PROBE_TREE_KILL_TIMEOUT_SECONDS):
                with suppress(OSError):
                    proc.kill()
        finally:
            with suppress(OSError, subprocess.TimeoutExpired):
                proc.wait(timeout=PROBE_EXIT_WAIT_SECONDS)

    def finish(self, proc: subprocess.Popen[str]) -> None:
        with self._lock:
            if proc in self._processes:
                self._terminate(proc, self._processes.pop(proc))

    def stop(self) -> None:
        self._stopped.set()
        self.close()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            while self._processes:
                proc, job = self._processes.popitem()
                self._terminate(proc, job)


_synchronous_probe_owners: dict[subprocess.Popen[str], ProbeCancellation] = {}


def spawn_probe(cancellation: ProbeCancellation | None, command: Sequence[str], **options: Any) -> subprocess.Popen[str]:
    if cancellation is not None:
        return cancellation.spawn(command, **options)
    if os.name == "nt":
        owner = ProbeCancellation()
        proc = owner.spawn(command, **options)
        _synchronous_probe_owners[proc] = owner
        return proc
    return subprocess.Popen(command, **options)


def run_text_probe(command: Sequence[str], *, timeout: float, cancellation: ProbeCancellation | None,
                   **options: Any) -> subprocess.CompletedProcess[str]:
    if cancellation is None:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False, **options)
    options.setdefault("stdin", subprocess.DEVNULL)
    proc = cancellation.spawn(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **options)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
    finally:
        cancellation.finish(proc)


def finish_probe(proc: subprocess.Popen[str], cancellation: ProbeCancellation | None) -> None:
    if cancellation is None and os.name == "nt":
        cancellation = _synchronous_probe_owners.pop(proc, None)
    if cancellation is None:
        _cleanup_probe(proc)
    else:
        cancellation.finish(proc)


def _cleanup_probe(proc: subprocess.Popen[str]) -> None:
    with suppress(Exception):  # best-effort cleanup preserves the probe result
        if os.name == "nt":
            if getattr(proc, "returncode", None) is None:
                proc.kill()
        else:
            kill_process_tree(proc)


def valid_fraction(value: object) -> TypeGuard[int | float]:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and 0.0 <= value <= 1.0 and math.isfinite(value))


class UnsupportedTimezoneError(ValueError):
    def __init__(self, tz_name: str) -> None:
        super().__init__(f"unsupported timezone: {tz_name}")
        self.tz_name = tz_name


class TimezoneDatabaseMissingError(RuntimeError):
    """IANA timezone data is unavailable; install the declared tzdata dependency."""


_KNOWN_TZ_ABBREVIATIONS: dict[str, timezone] = {
    "EDT": timezone(timedelta(hours=-4)),
    "EST": timezone(timedelta(hours=-5)),
    "CDT": timezone(timedelta(hours=-5)),
    "CST": timezone(timedelta(hours=-6)),
    "MDT": timezone(timedelta(hours=-6)),
    "MST": timezone(timedelta(hours=-7)),
    "PDT": timezone(timedelta(hours=-7)),
    "PST": timezone(timedelta(hours=-8)),
    "AKDT": timezone(timedelta(hours=-8)),
    "AKST": timezone(timedelta(hours=-9)),
    "HDT": timezone(timedelta(hours=-9)),
    "HST": timezone(timedelta(hours=-10)),
    "UTC": timezone.utc,
    "GMT": timezone.utc,
    "BST": timezone(timedelta(hours=1)),
    "CET": timezone(timedelta(hours=1)),
    "CEST": timezone(timedelta(hours=2)),
    "EET": timezone(timedelta(hours=2)),
    "EEST": timezone(timedelta(hours=3)),
    "WET": timezone.utc,
    "WEST": timezone(timedelta(hours=1)),
    "IST": timezone(timedelta(hours=5, minutes=30)),
    "KST": timezone(timedelta(hours=9)),
    "JST": timezone(timedelta(hours=9)),
    "HKT": timezone(timedelta(hours=8)),
    "SGT": timezone(timedelta(hours=8)),
    "AEST": timezone(timedelta(hours=10)),
    "AEDT": timezone(timedelta(hours=11)),
    "ACST": timezone(timedelta(hours=9, minutes=30)),
    "ACDT": timezone(timedelta(hours=10, minutes=30)),
    "AWST": timezone(timedelta(hours=8)),
    "NZST": timezone(timedelta(hours=12)),
    "NZDT": timezone(timedelta(hours=13)),
}


def resolve_explicit_tz(tz_name: Optional[str], default_tz: Optional[tzinfo]) -> Optional[tzinfo]:
    if not tz_name:
        return default_tz
    raw = tz_name.strip()
    upper = raw.upper()
    if upper in _KNOWN_TZ_ABBREVIATIONS:
        return _KNOWN_TZ_ABBREVIATIONS[upper]
    try:
        return ZoneInfo(raw)
    except ZoneInfoNotFoundError as exc:
        # A missing database and an unknown key both raise the same exception.
        # Probe a standard IANA key to distinguish the environment from the input.
        try:
            ZoneInfo("Etc/UTC")
        except ZoneInfoNotFoundError:
            raise TimezoneDatabaseMissingError("timezone_database_missing") from exc
        raise UnsupportedTimezoneError(raw) from exc
    except ValueError as exc:
        raise UnsupportedTimezoneError(raw) from exc


def json_object_from_output(text: str) -> Optional[dict[str, Any]]:
    """First JSON object in probe stdout (whole text, else last-line-first, tolerating a launcher prefix line)."""
    for candidate in (text.strip(), *(line.strip() for line in reversed(text.split("\n")))):  # LF only (#373)
        if not candidate:
            continue
        try:
            raw = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(raw, dict):
            return cast(dict[str, Any], raw)
    return None


def fail_closed(
    ids: IdSource,
    instance_id: str,
    profile_id: str,
    state: EvidenceState,
    observed_at: int,
    freshness_ttl: int,
    peer: str = "cc",
    evidence_ref_override: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
    source_tag_override: Optional[str] = None,
) -> UsageObserved:
    if peer == "cx":
        source_tag = "codex_app_server"
        provider_id = "peerhub.telemetry.cx"
        evidence_ref = EvidenceRef("probe:cx:app-server")
    elif peer == "ag":
        source_tag = "agy_statusline"
        provider_id = "peerhub.telemetry.ag"
        evidence_ref = EvidenceRef("probe:ag:statusline")
    else:
        source_tag = "claude_cli_usage"
        provider_id = "peerhub.telemetry.cc"
        evidence_ref = EvidenceRef("probe:cc:usage")

    evidence = EvidenceValue[UsageMeasurement](
        state=state,
        source_tag=source_tag_override or source_tag,
        provider_id=provider_id,
        provider_version="1.0",
        observed_at=observed_at,
        captured_at=observed_at,
        freshness_ttl=freshness_ttl,
        evidence_ref=EvidenceRef(evidence_ref_override) if evidence_ref_override else evidence_ref,
        value=None,
    )
    return UsageObserved(
        observation_id=ids.new_id("usage-observation"),
        instance_id=instance_id,
        profile_id=profile_id,
        evidence=evidence,
        extra=dict(extra or {}),
    )


def absent_pool(ids: IdSource, instance_id: str, profile_id: str, peer: str, observed_at: int, freshness_ttl: int,
                 pool_scope: str, plan: str = "unknown") -> UsageObserved:
    return fail_closed(ids, instance_id, profile_id, EvidenceState.ABSENT, observed_at, freshness_ttl, peer=peer,
                        extra={"pool_scope": pool_scope, "reason": f"{NOT_REPORTED_REASON}; plan={plan}"})


# Account / login / plan probes (#329). Each reads one provider surface that spends no model turn and
# keeps ONLY normalized fields: nothing identifying (email, org, account id, token, raw text) is returned,
# so nothing identifying can be persisted. UNKNOWN = could not be determined; "n/a" = the provider does not expose it.
@dataclass(frozen=True)
class AccountObserved:
    evidence_ref: str
    state: EvidenceState
    source_tag: str
    observed_at: int
    payload: dict[str, Any]
    warning: Optional[str] = None


CLAUDE_ACCOUNT_REF = "probe:cc:auth-status"
CODEX_ACCOUNT_REF = "probe:cx:account-read"
AGY_ACCOUNT_REF = "probe:ag:credits"


ACCOUNT_REFS = {"cc": CLAUDE_ACCOUNT_REF, "cx": CODEX_ACCOUNT_REF, "ag": AGY_ACCOUNT_REF}


def account_payload(evidence_ref: str, *, logged_in: object, plan: Optional[str], plan_status: str, expires: Optional[int] = None,
                     expiry_status: str = "n/a", auth_method: Optional[str] = None,
                     endpoint_access: Optional[str] = None) -> dict[str, Any]:
    """The whole stored account fact. Nothing identity-derived exists in it (no email, org, id, hash or reference)."""
    return {"logged_in": logged_in, "plan": plan, "plan_status": plan_status, "login_token_expires_at": expires,
            "login_expiry_status": expiry_status, "auth_method": auth_method,
            "endpoint_access": endpoint_access, "evidence_ref": evidence_ref}


def account_failure(ref: str, source: str, observed_at: int, state: EvidenceState, reason: str,
                     warning: Optional[str] = None, evidence_ref: Optional[str] = None, error_type: Optional[str] = None) -> AccountObserved:
    payload: dict[str, Any] = {"condition": "account_probe_failed", "reason": reason, "evidence_ref": evidence_ref or ref}
    if error_type:
        payload["error_type"] = error_type
    return AccountObserved(ref, state, source, observed_at, payload, warning)
