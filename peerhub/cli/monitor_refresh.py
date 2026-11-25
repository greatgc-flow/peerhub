"""Schedule quota refreshes for CLI watch, suggestion and dispatch."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from threading import Lock, Thread
from time import time
from typing import Any, Callable, ClassVar, Mapping, Sequence, cast

from peerhub.core.peer_kinds import ALIASES
from peerhub.core.quota_policy import QUOTA_TTL_SECONDS

MONITOR_REFRESH_JOIN_SECONDS = .75
MONITOR_REFRESH_LEAD_SECONDS = 90
MONITOR_TICK_SECONDS = 1.0
MONITOR_QUOTA_MAX_AGE_SECONDS = QUOTA_TTL_SECONDS - MONITOR_REFRESH_LEAD_SECONDS
MONITOR_PROBE_MAX_TIMEOUT_SECONDS = MONITOR_REFRESH_LEAD_SECONDS - MONITOR_TICK_SECONDS
assert 0 < MONITOR_PROBE_MAX_TIMEOUT_SECONDS < MONITOR_REFRESH_LEAD_SECONDS < QUOTA_TTL_SECONDS

QUOTA_REFRESH_COOLDOWN_SECONDS = 60
DISPATCH_QUOTA_WAIT_SECONDS = 1.0


def quota_workspace(db_path: str | Path) -> Path:
    database = Path(db_path).resolve()
    return database.parent.parent if database.parent.name == ".peerhub" else database.parent


def configured_pools(db_path: str | Path, peers: Sequence[str], kind: str) -> list[str] | None:
    """Return configured pools for the requested provider kind, or None if it was not requested."""
    if not any(ALIASES.get(peer) == kind for peer in peers):
        return None
    from peerhub.extensions.adapters.profiles import list_profiles
    profiles = list_profiles(quota_workspace(db_path))
    return sorted({str(pool) for profile in profiles if str(profile["profile"]).startswith(f"{kind}.")
                   for pool in cast(list[object], profile["fields"]["pools"]["value"] or [])})


def refresh_with_profile_pools(db_path: str | Path, peers: Sequence[str], **options: Any) -> dict[str, Any]:
    """Collect quota for the configured profile pools."""
    from peerhub.extensions.quota_capture import refresh_quota

    return refresh_quota(
        db_path, list(peers), cx_pools=configured_pools(db_path, peers, "cx"),
        cc_pools=configured_pools(db_path, peers, "cc"), ag_pools=configured_pools(db_path, peers, "ag"), **options)


def quota_expired(item: dict[str, Any]) -> bool:
    from peerhub.extensions.observation_model import QUOTA_MAX_AGE_SECONDS

    age = item.get("age_seconds")
    return age is None or age > QUOTA_MAX_AGE_SECONDS


def _recent_quota_peers(db_path: str | Path, max_age: float) -> set[str]:
    from peerhub.extensions.diag_quota import quota_items

    return {item["subject_ref"] for item in quota_items(db_path)
            if item["subject_ref"] is not None and item["age_seconds"] is not None and item["age_seconds"] <= max_age}


def try_quota_refresh(db_path: str | Path, peers: Sequence[str]) -> None:
    """A failed collection leaves quota unknown; ask delivery still has its own result."""
    try:
        from peerhub.extensions import quota_capture

        with quota_capture.quota_refresh_lock(db_path) as acquired:
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
    """Preserve the automatic collection cadence of the scripted monitor."""
    from peerhub.extensions import quota_capture

    peers = set(args.peers)
    peers.update(ALIASES.get(peer, peer) for peer in args.peers)
    try:
        if peers & _recent_quota_peers(args.db, QUOTA_TTL_SECONDS):
            return None
        with quota_capture.quota_refresh_lock(args.db) as acquired:
            if not acquired or peers & _recent_quota_peers(args.db, QUOTA_TTL_SECONDS):
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
            from peerhub.extensions.diag_quota import quota_items

            if not any(not quota_expired(item) for item in quota_items(db_path, peer=peer)
                       if item["subject_ref"] == peer):
                try_quota_refresh(db_path, [peer])
        except Exception:
            pass

    worker = Thread(target=refresh, daemon=True)
    worker.start()
    worker.join(DISPATCH_QUOTA_WAIT_SECONDS)


def agy_token_command(result: dict[str, Any]) -> str | None:
    """The agy slash command that consumed model tokens in this refresh (`/credits` for an account probe, else `/usage`), or None."""
    hits = [o for o in result.get("observations", []) if o.get("payload", {}).get("evidence_ref") == "agy_usage_consumed_tokens"]
    if not hits:
        return None
    return "/credits" if any(o.get("kind") == "account" for o in hits) else "/usage"


def agy_token_stop_message(command: str) -> str:
    return (f"error: agy {command} consumed model tokens; stopping watch mode so quota is not burned every interval "
            "(pass --allow-agy-token-use to continue)")


def read_quota_readings(db: str | Path) -> list[dict[str, Any]]:
    from peerhub.extensions.diag import ReadonlyDiag

    section = ReadonlyDiag(str(db)).render(["observations"]).sections.get("observations")
    return [item for item in section.data.get("items", []) if item["kind"] == "quota"] if section else []


def required_quota_pools(db: str | Path, peers: Sequence[str]) -> dict[str, set[str]]:
    try:
        configured = {kind: configured_pools(db, peers, kind) or [] for kind in ("cc", "cx", "ag")}
    except (OSError, ValueError):
        configured = {}  # The worker reports the configuration error if it needs to collect.
    required: dict[str, set[str]] = {}
    for peer in peers:
        kind = ALIASES.get(peer, peer)
        required[peer] = {f"quota:{kind}:{pool}" for pool in configured.get(kind, [])}
    return required


class RefreshSchedule:
    """Schedule on monotonic deadlines derived from the last committed readings."""

    def __init__(self, required: Mapping[str, set[str]], *, forced_interval: float | None = None) -> None:
        self.required = dict(required)
        self._forced_interval = forced_interval
        self._forced_at = dict.fromkeys(required, float("-inf"))
        self._evidence_due: dict[str, float] = {}
        self._retry_at: dict[str, float] = {}
        self._failed_at: dict[str, float] = {}
        self._age_windows: list[tuple[float, float]] = []

    def update(self, readings: Sequence[Mapping[str, Any]], now: float) -> None:
        from peerhub.extensions.observation_model import ABSENT_FACT_REASONS
        from peerhub.extensions.quota_windows import quota_window_remaining

        by_peer: dict[str, dict[str, Mapping[str, Any]]] = {}
        for item in readings:
            if item.get("kind") == "quota" and (pool := item.get("resource_pool_ref")):
                by_peer.setdefault(item["subject_ref"], {})[pool] = item
        age_windows: list[tuple[float, float]] = []
        wall_at = time()
        for peer, required in self.required.items():
            pools = by_peer.get(peer, by_peer.get(ALIASES.get(peer, peer), {}))
            usable: dict[str, float] = {}
            for pool, item in pools.items():
                age, payload = item.get("age_seconds"), item["payload"]
                if age is None:
                    continue
                if item["stored_state"] == "MEASURED" and payload.get("remaining_fraction") is not None:
                    if not required or pool in required:  # a pool the peer no longer needs must not age the title
                        remaining = quota_window_remaining(item, wall_at)
                        age_windows.append((now - age, float("inf") if remaining is None else now + remaining))
                    usable[pool] = age
                elif item["stored_state"] == "ABSENT" and str(payload.get("reason", "")).startswith(ABSENT_FACT_REASONS):
                    usable[pool] = age
            needed = required or pools.keys()
            self._evidence_due[peer] = (now + MONITOR_QUOTA_MAX_AGE_SECONDS - max(usable[p] for p in needed)
                                        if needed and all(p in usable for p in needed) else now)
        self._age_windows = age_windows

    def oldest_age(self, now: float) -> float | None:
        return max((now - measured_at for measured_at, expires_at in self._age_windows if now <= expires_at), default=None)

    def _due_at(self, peer: str) -> float:
        evidence_due = self._evidence_due.get(peer, float("-inf"))
        if peer in self._retry_at:
            # A failed peer waits out its cooldown; a merely deferred one (the lock was held elsewhere) is also not due
            # while fresh evidence says so, or a refresh by another process would restart jobs every tick.
            return self._retry_at[peer] if peer in self._failed_at else max(self._retry_at[peer], evidence_due)
        return min(evidence_due, self._forced_at[peer]) if self._forced_interval is not None else evidence_due

    def due(self, now: float) -> tuple[str, ...]:
        return tuple(peer for peer in self.required if self._due_at(peer) <= now)

    def next_in(self, now: float) -> float | None:
        return max(0, min(map(self._due_at, self.required)) - now) if self.required else None

    def failed(self, peer: str) -> bool:
        return peer in self._failed_at

    def failed_ago(self, now: float) -> float | None:
        return now - max(self._failed_at.values()) if self._failed_at else None

    def retry_in(self, now: float) -> float | None:
        return max(0, min(map(self._due_at, self._failed_at)) - now) if self._failed_at else None

    def deferred(self, peers: Sequence[str], now: float) -> None:
        self._retry_at.update(dict.fromkeys(peers, now + QUOTA_REFRESH_COOLDOWN_SECONDS))

    def completed(self, peers: Sequence[str], result: Mapping[str, Any], now: float) -> None:
        reported: dict[str, set[str]] = {}
        failed_peers: set[str] = set()
        for item in result.get("observations", []):
            if item.get("kind") != "quota":
                continue
            peer = item["subject_ref"]
            pool = item.get("resource_pool_ref")
            if pool and item["state"] in {"MEASURED", "ABSENT"}:
                reported.setdefault(peer, set()).add(pool)
            else:
                failed_peers.add(peer)
        for peer in peers:
            pools = reported.get(peer)
            complete = pools is not None and self.required[peer] <= pools
            if result["status"] not in {"OK", "PARTIAL"} or peer in failed_peers or not complete or self._evidence_due[peer] <= now:
                self._failed_at[peer] = now
                self.deferred((peer,), now)
            else:
                self._failed_at.pop(peer, None)
                self._retry_at.pop(peer, None)
            if self._forced_interval is not None:
                self._forced_at[peer] = now + self._forced_interval


class RefreshJob:
    """One daemon collects and commits quota; the UI observes its result after exit."""

    _lock = Lock()
    _active: ClassVar[RefreshJob | None] = None

    def __init__(self, args: argparse.Namespace, peers: tuple[str, ...], required: Mapping[str, set[str]], *,
                 force: bool, retry: tuple[str, ...]) -> None:
        from peerhub.extensions.quota_probes import ProbeCancellation

        self.peers = peers
        self.result: dict[str, Any] | None = None
        self._cancel = ProbeCancellation()
        self._worker = Thread(target=self._run, args=(args, required, force, retry), name="peerhub-quota-refresh", daemon=True)

    @classmethod
    def start(cls, args: argparse.Namespace, peers: tuple[str, ...], *, force: bool,
              required: Mapping[str, set[str]] | None = None, retry: tuple[str, ...] = ()) -> RefreshJob | None:
        job = cls(args, peers, required if required is not None else required_quota_pools(args.db, peers),
                  force=force, retry=retry)
        try:
            with cls._lock:
                if cls._active is not None:
                    return None
                cls._active = job
            job._worker.start()
        except BaseException:
            job._cancel.stop()  # Thread.start can be interrupted after the thread has been launched.
            job._release()
            raise
        return job

    def _release(self) -> None:
        cls = type(self)
        with cls._lock:
            if cls._active is self:
                cls._active = None

    def _run(self, args: argparse.Namespace, required: Mapping[str, set[str]], force: bool, retry: tuple[str, ...]) -> None:
        from peerhub.extensions import quota_capture

        try:
            with quota_capture.quota_refresh_lock(args.db, cancelled=self._cancel.cancelled) as acquired, closing(self._cancel):
                if not acquired or self.cancelled():
                    return
                if not force:
                    check = RefreshSchedule({peer: required[peer] for peer in self.peers})
                    check.update(read_quota_readings(args.db), 0)
                    due = set(check.due(0)) | set(retry)
                    self.peers = tuple(peer for peer in self.peers if peer in due)
                timeout = args.timeout
                if timeout >= MONITOR_REFRESH_LEAD_SECONDS:
                    timeout = MONITOR_PROBE_MAX_TIMEOUT_SECONDS
                self.result = quota_capture.refresh_each_peer(
                    self.peers, cancellation=self._cancel,
                    collect=lambda peer: refresh_with_profile_pools(
                        args.db, (peer,), sys_dir=args.sys_dir, deadline_sec=timeout, cancellation=self._cancel))
        except Exception as exc:
            if not self.cancelled():
                self.result = {"status": "FAILED", "warnings": [type(exc).__name__]}
        finally:
            self._release()

    def done(self) -> bool:
        return not self._worker.is_alive()

    def cancelled(self) -> bool:
        return self._cancel.cancelled()

    def stop(self) -> None:
        self._cancel.stop()
        self._worker.join(MONITOR_REFRESH_JOIN_SECONDS)
