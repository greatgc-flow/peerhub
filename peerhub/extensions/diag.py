"""PeerHub M1 First-Party Extension: Readonly Diag (observer only; its own module so ARCH-004 can check the whole module).

Allowed: read Stream/Record/Offset/Observation/log. Forbidden: append/refresh/interrupt/resume/repair/restart/route.
- Every handle is a read-only SQLite URI handle with `PRAGMA query_only=ON`, verified; a handle that is not read-only fails the render closed.
- One render = ONE read transaction (one committed snapshot across all sections, also under a concurrent writer).
- Sections are independent: a malformed observation / unreadable log degrades only its own section (TD-07).
- No migration, repair, recreate or retry: a store read failure is reported as FAILED/ERROR (TD-15).
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from peerhub.m1.schema_version import SUPPORTED_SCHEMA_VERSION, SchemaVersionError, future_schema_message
from peerhub.extensions.observation_model import (
    FreshnessPolicy,
    ObservationCorruptError,
    effective_us,
    evaluate,
    read_only_authorizer,
    row_to_observation,
)

SECTION_ORDER = ("peers", "streams", "resource_pools", "observations", "log")
LOG_TAIL_BYTES = 65536
LOG_TAIL_LINES = 50


class DiagReadOnlyError(RuntimeError):
    """The connection could not be proven read-only; nothing was read through it."""


@dataclass(frozen=True)
class Section:
    name: str
    status: str  # OK | ERROR | UNAVAILABLE
    data: dict[str, Any] = field(default_factory=dict)
    errors: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class DiagnosticReport:
    status: str  # OK | PARTIAL (some section ERROR) | FAILED (no snapshot could be established)
    read_at: float
    snapshot: dict[str, Any]
    sections: dict[str, Section]
    error: str | None = None


def _default_factory(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _default_open_log(path: Path):
    return open(path, "rb")


def _err(e: BaseException, **extra: Any) -> dict[str, Any]:
    return {**extra, "error": f"{type(e).__name__}: {e}"}


class ReadonlyDiag:
    def __init__(self, db_path: str | Path, *, log_path: str | Path | None = None, policy: FreshnessPolicy | None = None,
                 connection_factory: Callable[[str], Any] | None = None, open_log: Callable[[Path], Any] | None = None,
                 section_hook: Callable[[str], None] | None = None) -> None:
        """`connection_factory`, `open_log`, `section_hook` are test seams; the factory's handle is forced to query_only and verified."""
        self.db_path = str(db_path)
        self.log_path = None if log_path is None else Path(log_path)
        self.policy = policy or FreshnessPolicy()
        self._factory = connection_factory or _default_factory
        self._open_log = open_log or _default_open_log
        self._hook = section_hook

    # ------------------------------------------------------------------ connection
    def _open(self):
        conn = self._factory(self.db_path)
        try:
            conn.execute("PRAGMA query_only = ON")
            row = conn.execute("PRAGMA query_only").fetchone()
            if not row or row[0] != 1:
                raise DiagReadOnlyError("connection is not read-only (query_only could not be enabled)")
            conn.set_authorizer(read_only_authorizer(allow_transactions=True))  # runtime deny of every non-read statement
            conn.execute("BEGIN")
            conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()  # pins the read snapshot now
            found = conn.execute("PRAGMA user_version").fetchone()[0]
            if found > SUPPORTED_SCHEMA_VERSION:  # never interpret a schema this build does not understand (MIG-003)
                raise SchemaVersionError(future_schema_message(found, SUPPORTED_SCHEMA_VERSION))
            rows = conn.execute("PRAGMA quick_check").fetchall()  # read-only probe: a corrupt authoritative store is FAILED, never half-reported
            if [r[0] for r in rows] != ["ok"]:
                raise sqlite3.DatabaseError(f"integrity check failed: {[r[0] for r in rows[:3]]}")
        except BaseException:
            try:
                conn.close()
            except Exception:
                pass
            raise
        return conn

    # ------------------------------------------------------------------ sections (all read from the same snapshot)
    @staticmethod
    def _peers(conn, _us) -> Section:
        rows = conn.execute("SELECT peer_id, display_name, adapter_ref, created_at FROM peers ORDER BY peer_id").fetchall()
        return Section("peers", "OK", {"peers": [dict(zip(("peer_id", "display_name", "adapter_ref", "created_at"), tuple(r))) for r in rows]})

    @staticmethod
    def _streams(conn, _us) -> Section:
        out = []
        for s in conn.execute("SELECT stream_id, title, state, revision FROM streams ORDER BY stream_id").fetchall():
            sid = s[0]
            head = conn.execute("SELECT COALESCE(MAX(position), 0) FROM records WHERE stream_id = ?", (sid,)).fetchone()[0]
            count = conn.execute("SELECT COUNT(*) FROM records WHERE stream_id = ?", (sid,)).fetchone()[0]
            members = [r[0] for r in conn.execute("SELECT peer_id FROM stream_members WHERE stream_id = ? ORDER BY peer_id", (sid,)).fetchall()]
            offsets = {r[0]: r[1] for r in conn.execute(
                "SELECT peer_id, read_through_position FROM offsets WHERE stream_id = ? ORDER BY peer_id", (sid,)).fetchall()}
            out.append({"stream_id": sid, "title": s[1], "state": s[2], "revision": s[3], "head_position": head,
                        "record_count": count, "members": members, "offsets": offsets})
        return Section("streams", "OK", {"streams": out})

    @staticmethod
    def _pools(conn, _us) -> Section:
        counts = {r[0]: r[1] for r in conn.execute(
            "SELECT resource_pool_ref, COUNT(*) FROM observations WHERE resource_pool_ref IS NOT NULL GROUP BY resource_pool_ref").fetchall()}
        rows = conn.execute("SELECT resource_pool_id, provider, kind, metadata_json FROM resource_pools ORDER BY resource_pool_id").fetchall()
        return Section("resource_pools", "OK", {"pools": [
            {"resource_pool_id": r[0], "provider": r[1], "kind": r[2], "metadata_json": r[3], "observation_count": counts.get(r[0], 0)} for r in rows]})

    def _observations(self, conn, read_us) -> Section:
        rows = conn.execute(
            "SELECT capture_seq, observation_id, subject_ref, resource_pool_ref, kind, source, state, payload_json, observed_at, captured_at "
            "FROM observations ORDER BY capture_seq").fetchall()
        best: dict[tuple, Any] = {}
        errors: list[dict[str, Any]] = []
        for r in rows:
            try:
                obs, seq = row_to_observation(r)
            except ObservationCorruptError as e:  # one bad row degrades this section only; nothing is repaired or skipped silently
                errors.append(_err(e, observation_id=r["observation_id"], capture_seq=r["capture_seq"]))
                continue
            view = evaluate(obs, seq, read_us, self.policy)
            key = (obs.subject_ref, obs.kind, obs.resource_pool_ref)
            prev = best.get(key)
            if prev is None or self._newer(view, prev):
                best[key] = view
        items = []
        for key in sorted(best, key=lambda k: (k[0], k[1], k[2] or "")):
            v = best[key]
            o = v.observation
            items.append({"observation_id": o.observation_id, "subject_ref": o.subject_ref, "kind": o.kind, "resource_pool_ref": o.resource_pool_ref,
                          "source": o.source, "stored_state": v.stored_state.value, "state": v.state.value, "age_seconds": v.age_seconds,
                          "ttl_seconds": v.ttl_seconds, "basis": v.basis, "skew_seconds": v.skew_seconds, "capture_seq": v.capture_seq,
                          "observed_at": o.observed_at, "captured_at": o.captured_at, "payload": o.payload})
        return Section("observations", "ERROR" if errors else "OK", {"items": items}, tuple(errors))

    @staticmethod
    def _newer(a, b) -> bool:
        """TD-13/TD-23 ordering: effective capture time, then persisted capture ordinal."""
        ka, kb = (effective_us(a.observation), a.capture_seq), (effective_us(b.observation), b.capture_seq)
        return ka > kb

    def _log(self, _conn, _us) -> Section:
        if self.log_path is None:
            return Section("log", "UNAVAILABLE", {}, ({"error": "log source not configured"},))
        try:
            with self._open_log(self.log_path) as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - LOG_TAIL_BYTES))
                raw = f.read()
        except FileNotFoundError as e:
            return Section("log", "UNAVAILABLE", {}, (_err(e),))
        except (OSError, ValueError) as e:  # PermissionError, IsADirectoryError, I/O errors: explicit, no chmod/repair
            return Section("log", "ERROR", {}, (_err(e),))
        lines = raw.decode("utf-8", errors="replace").splitlines()
        if size > LOG_TAIL_BYTES and lines:
            lines = lines[1:]  # first line of a truncated tail may be partial
        return Section("log", "OK", {"lines": lines[-LOG_TAIL_LINES:], "size_bytes": size})

    # ------------------------------------------------------------------ public views
    def render(self, sections: list[str] | tuple[str, ...] | None = None, read_at: float | None = None) -> DiagnosticReport:
        names = list(SECTION_ORDER) if sections is None else list(sections)
        unknown = [n for n in names if n not in SECTION_ORDER]
        if unknown:
            raise ValueError(f"unknown diag section(s): {unknown}")
        now = time.time() if read_at is None else read_at
        read_us = round(now * 1_000_000)
        try:
            conn = self._open()
        except (sqlite3.Error, OSError, DiagReadOnlyError, SchemaVersionError) as e:
            return DiagnosticReport("FAILED", now, {}, {}, f"{type(e).__name__}: {e}")
        impl = {"peers": self._peers, "streams": self._streams, "resource_pools": self._pools, "observations": self._observations, "log": self._log}
        out: dict[str, Section] = {}
        try:
            snapshot = {}
            for key, one in (("records_total", lambda: conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]),
                             ("observations_total", lambda: conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0])):
                try:
                    snapshot[key] = one()
                except sqlite3.Error:
                    snapshot[key] = None
            for name in names:
                try:
                    out[name] = impl[name](conn, read_us)
                except sqlite3.Error as e:  # section-level isolation: other sections stay readable
                    out[name] = Section(name, "ERROR", {}, (_err(e),))
                if self._hook is not None:
                    self._hook(name)
        finally:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            conn.close()
        status = "PARTIAL" if any(s.status == "ERROR" for s in out.values()) else "OK"
        return DiagnosticReport(status, now, snapshot, out)

    def inspect_stream_health(self, stream_id: str) -> dict[str, Any]:
        rep = self.render(["streams"])
        if rep.status == "FAILED":
            return {"status": "ERROR", "stream_id": stream_id, "error": rep.error}
        sec = rep.sections["streams"]
        if sec.status != "OK":
            return {"status": "ERROR", "stream_id": stream_id, "error": sec.errors[0]["error"]}
        for s in sec.data["streams"]:
            if s["stream_id"] == stream_id:
                return {"status": "OK", **s}
        return {"status": "NOT_FOUND", "stream_id": stream_id}
