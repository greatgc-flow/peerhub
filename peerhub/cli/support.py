"""Shared CLI entry points for reading and collecting quota evidence."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from time import monotonic, sleep
from typing import Any, BinaryIO, Callable, Generator, Sequence, cast

_PEER_ALIASES = {"cx": {"cx", "codex"}, "cc": {"cc", "claude"}, "ag": {"ag", "agy"}}
MONITOR_QUOTA_MAX_AGE_SECONDS = 5 * 60
QUOTA_LOCK_WAIT_SECONDS = 3.0
QUOTA_REFRESH_COOLDOWN_SECONDS = 60
DISPATCH_QUOTA_WAIT_SECONDS = 1.0


def quota_workspace(db_path: str | Path) -> Path:
    database = Path(db_path).resolve()
    return database.parent.parent if database.parent.name == ".peerhub" else database.parent


def configured_pools(db_path: str | Path, peers: Sequence[str], kind: str) -> list[str] | None:
    """Resolve profile configuration at the CLI boundary, outside Observation probes (None: kind not requested)."""
    if not any(peer in _PEER_ALIASES[kind] for peer in peers):
        return None
    from peerhub.extensions.adapters.profiles import list_profiles
    profiles = list_profiles(quota_workspace(db_path))
    return sorted({str(pool) for profile in profiles if str(profile["profile"]).startswith(f"{kind}.")
                   for pool in cast(list[object], profile["fields"]["pools"]["value"] or [])})


def configured_cx_pools(db_path: str | Path, peers: Sequence[str]) -> list[str] | None:
    return configured_pools(db_path, peers, "cx")


def refresh_with_profile_pools(db_path: str | Path, peers: Sequence[str], **options: Any) -> dict[str, Any]:
    """The CLI entry to the quota collector. Resolve configured pools consistently for all callers."""
    from peerhub.extensions import quota_capture
    return quota_capture.refresh_quota(
        db_path, list(peers), cx_pools=configured_pools(db_path, peers, "cx"),
        cc_pools=configured_pools(db_path, peers, "cc"), ag_pools=configured_pools(db_path, peers, "ag"), **options)


def quota_items(db_path: str | Path, *, peer: str | None = None) -> list[dict[str, Any]]:
    from peerhub.extensions.diag_quota import quota_report
    from peerhub.extensions.observation_model import QUOTA_MAX_AGE_SECONDS, FreshnessPolicy

    # Observation freshness compares age >= TTL at microsecond precision. This gate includes exactly 15 minutes.
    policy = FreshnessPolicy(by_kind={"quota": QUOTA_MAX_AGE_SECONDS + .000001})
    report = quota_report(db_path, peer=peer, policy=policy)
    return [item for group in report["pools"] for item in group["items"] if item["kind"] == "quota"]


def quota_expired(item: dict[str, Any]) -> bool:
    from peerhub.extensions.observation_model import QUOTA_MAX_AGE_SECONDS

    age = item.get("age_seconds")
    return age is None or age > QUOTA_MAX_AGE_SECONDS


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
def quota_refresh_lock(db_path: str | Path) -> Generator[bool, None, None]:
    """Hold a PID lock until the refresh finishes or its process exits."""
    import psutil
    from peerhub.extensions.batch import store_scratch_dir

    scratch = store_scratch_dir(db_path)
    scratch.mkdir(parents=True, exist_ok=True)
    deadline = monotonic() + QUOTA_LOCK_WAIT_SECONDS
    # Keep a stable file identity so two contenders cannot lock different files during dead-owner recovery.
    with (scratch / "quota-refresh.lock").open("a+b") as file:
        while not _try_file_lock(file):
            remaining = deadline - monotonic()
            if remaining <= 0:
                yield False
                return
            sleep(min(.05, remaining))
        file.seek(0)
        raw = file.read(32).strip()
        owner = int(raw) if raw.isdigit() else 0
        while 0 < owner <= 0x7fffffff and psutil.pid_exists(owner):
            remaining = deadline - monotonic()
            if remaining <= 0:
                yield False
                return
            sleep(min(.05, remaining))
        file.seek(0)
        file.truncate()
        file.write(str(os.getpid()).encode("ascii"))
        file.flush()
        try:
            yield True
        finally:
            file.seek(0)
            file.truncate()  # Closing releases the OS lock. An interrupted owner leaves its PID for the next caller.


def _recent_quota_peers(db_path: str | Path, max_age: float) -> set[str]:
    return {item["subject_ref"] for item in quota_items(db_path)
            if item["subject_ref"] is not None and item["age_seconds"] is not None and item["age_seconds"] <= max_age}


def try_quota_refresh(db_path: str | Path, peers: Sequence[str]) -> None:
    """A failed collection leaves quota unknown; ask delivery still has its own result."""
    try:
        with quota_refresh_lock(db_path) as acquired:
            if acquired:
                recent = _recent_quota_peers(db_path, QUOTA_REFRESH_COOLDOWN_SECONDS)
                due = [peer for peer in peers if peer not in recent]
                if due:
                    refresh_with_profile_pools(db_path, due)
    except Exception:
        pass


def monitor_auto_probe(args: argparse.Namespace) -> bool:
    return (not args.probe and not args.no_probe and sys.stdout.isatty()
            and os.environ.get("PEERHUB_MONITOR_PROBE", "").strip().lower() not in {"off", "0", "false", "no"})


def refresh_monitor_quota(args: argparse.Namespace, before_probe: Callable[[], None] | None = None) -> dict[str, Any] | None:
    """Start one automatic cycle only when the newest reading for the selected peers is old."""
    peers = set(args.peers)
    for kind, aliases in _PEER_ALIASES.items():
        if peers & aliases:
            peers.add(kind)
    try:
        if peers & _recent_quota_peers(args.db, MONITOR_QUOTA_MAX_AGE_SECONDS):
            return None
        with quota_refresh_lock(args.db) as acquired:
            if not acquired or peers & _recent_quota_peers(args.db, MONITOR_QUOTA_MAX_AGE_SECONDS):
                return None
            if before_probe is not None:
                before_probe()
            return refresh_with_profile_pools(args.db, args.peers, sys_dir=args.sys_dir,
                                              deadline_sec=args.timeout, retry_timeouts=True)
    except (OSError, sqlite3.Error):
        return None


def refresh_dispatch_quota(db_path: Path, peer: str) -> None:
    """Give advisory quota collection a short head start before dispatch."""
    def refresh() -> None:
        try:
            if not any(not quota_expired(item) for item in quota_items(db_path, peer=peer)
                       if item["subject_ref"] == peer):
                try_quota_refresh(db_path, [peer])
        except Exception:
            pass

    worker = Thread(target=refresh, daemon=True)
    worker.start()
    worker.join(DISPATCH_QUOTA_WAIT_SECONDS)


def print_warnings(result: dict[str, Any]) -> None:
    for w in result.get("warnings", []):
        print(f"warning: {w}", file=sys.stderr, flush=True)


def agy_token_command(result: dict[str, Any]) -> str | None:
    """The agy slash command that consumed model tokens in this refresh (`/credits` for an account probe, else `/usage`), or None."""
    hits = [o for o in result.get("observations", []) if o.get("payload", {}).get("evidence_ref") == "agy_usage_consumed_tokens"]
    if not hits:
        return None
    return "/credits" if any(o.get("kind") == "account" for o in hits) else "/usage"


def agy_token_use(result: dict[str, Any]) -> bool:
    return agy_token_command(result) is not None


def agy_token_stop_message(command: str) -> str:
    return (f"error: agy {command} consumed model tokens; stopping watch mode so quota is not burned every interval "
            "(pass --allow-agy-token-use to continue)")


def error_line(code: str, message: str, hint: str | None = None) -> None:
    hint = hint or f"peerhub explain {code}"
    print(f"[{code}] {' '.join(str(message).split())}; next: {' '.join(hint.split())}", file=sys.stderr)
