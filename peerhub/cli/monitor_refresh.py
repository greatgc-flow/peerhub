"""Per-peer scheduling and cancellable collection for continuous terminal watch."""
from __future__ import annotations

import argparse
from contextlib import closing
from pathlib import Path
from threading import Lock, Thread
from typing import Any, ClassVar, Mapping, Sequence

from peerhub.cli import support
from peerhub.extensions.diag import ReadonlyDiag
from peerhub.extensions.observation_model import ABSENT_FACT_REASONS
from peerhub.core.peer_kinds import ALIASES
from peerhub.extensions.quota_probes import ProbeCancellation

MONITOR_REFRESH_JOIN_SECONDS = .75


def read_quota_readings(db: str | Path) -> list[dict[str, Any]]:
    section = ReadonlyDiag(str(db)).render(["observations"]).sections.get("observations")
    return [item for item in section.data.get("items", []) if item["kind"] == "quota"] if section else []


def required_quota_pools(db: str | Path, peers: Sequence[str]) -> dict[str, set[str]]:
    try:
        configured = {kind: support.configured_pools(db, peers, kind) or [] for kind in ("cc", "cx", "ag")}
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

    def update(self, readings: Sequence[Mapping[str, Any]], now: float) -> None:
        by_peer: dict[str, dict[str, Mapping[str, Any]]] = {}
        for item in readings:
            if item.get("kind") == "quota" and (pool := item.get("resource_pool_ref")):
                by_peer.setdefault(item["subject_ref"], {})[pool] = item
        for peer, required in self.required.items():
            pools = by_peer.get(peer, by_peer.get(ALIASES.get(peer, peer), {}))
            usable: dict[str, float] = {}
            for pool, item in pools.items():
                age, payload = item.get("age_seconds"), item["payload"]
                if age is None:
                    continue
                if item["stored_state"] == "MEASURED" and payload.get("remaining_fraction") is not None:
                    usable[pool] = age
                elif item["stored_state"] == "ABSENT" and str(payload.get("reason", "")).startswith(ABSENT_FACT_REASONS):
                    usable[pool] = age
            needed = required or pools.keys()
            self._evidence_due[peer] = (now + support.MONITOR_QUOTA_MAX_AGE_SECONDS - max(usable[p] for p in needed)
                                        if needed and all(p in usable for p in needed) else now)

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
        self._retry_at.update(dict.fromkeys(peers, now + support.QUOTA_REFRESH_COOLDOWN_SECONDS))

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
        try:
            with support.quota_refresh_lock(args.db, cancelled=self._cancel.cancelled) as acquired, closing(self._cancel):
                if not acquired or self.cancelled():
                    return
                if not force:
                    check = RefreshSchedule({peer: required[peer] for peer in self.peers})
                    check.update(read_quota_readings(args.db), 0)
                    due = set(check.due(0)) | set(retry)
                    self.peers = tuple(peer for peer in self.peers if peer in due)
                timeout = args.timeout
                if timeout >= support.MONITOR_REFRESH_LEAD_SECONDS:
                    timeout = support.MONITOR_PROBE_MAX_TIMEOUT_SECONDS
                statuses: set[str] = set()
                observations: list[dict[str, Any]] = []
                warnings: list[str] = []
                for peer in self.peers:
                    if self.cancelled():
                        return
                    try:
                        result = support.refresh_with_profile_pools(
                            args.db, (peer,), sys_dir=args.sys_dir, cancellation=self._cancel,
                            deadline_sec=timeout)
                    except Exception as exc:
                        statuses.add("FAILED")
                        warnings.append(type(exc).__name__)
                        continue
                    statuses.add(result["status"])
                    observations.extend(result.get("observations", []))
                    warnings.extend(result.get("warnings", []))
                if statuses and not self.cancelled():
                    status = "OK" if statuses == {"OK"} else "FAILED" if statuses == {"FAILED"} else "PARTIAL"
                    self.result = {"status": status, "observations": observations, "warnings": warnings}
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
