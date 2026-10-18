"""PeerHub first-party extension: Readonly Diag (observer only; its own module so ARCH-004 can check the whole module).

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
from typing import cast, Any, Callable

from peerhub.core.schema_version import SUPPORTED_SCHEMA_VERSION, SchemaVersionError, future_schema_message
from peerhub.core.read_only_probe import probe_table_roots
from peerhub.extensions.observation_model import (
    FreshnessPolicy,
    ObservationCorruptError,
    ObservationView,
    effective_us,
    evaluate,
    read_only_authorizer,
    row_to_observation,
)

SECTION_ORDER = ("peers", "streams", "resource_pools", "observations", "log")
OPTIONAL_SECTIONS = ("activity",)  # computed on request only: the default render stays the five sections above
ACTIVITY_WINDOW = 50  # newest asks per peer that the activity section summarises
ASK_OK_STATUSES = ("delivered", "recovered_terminal")
LOG_TAIL_BYTES = 65536
LOG_TAIL_LINES = 50


class DiagReadOnlyError(RuntimeError):
    """The connection could not be proven read-only; nothing was read through it."""


@dataclass(frozen=True)
class Section:
    name: str
    status: str  # OK | ERROR | UNAVAILABLE
    data: dict[str, Any] = field(default_factory=dict[str, Any])
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


def integrity_check(db_path: str | Path) -> dict[str, Any]:
    """Run SQLite's full integrity scan only when explicitly requested."""
    conn = _default_factory(str(db_path))
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.set_authorizer(read_only_authorizer(allow_transactions=True))
        found = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if found > SUPPORTED_SCHEMA_VERSION:
            raise SchemaVersionError(future_schema_message(found, SUPPORTED_SCHEMA_VERSION))
        rows = [str(row[0]) for row in conn.execute("PRAGMA quick_check").fetchall()]
        has_rollups = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='observation_rollups'"
        ).fetchone() is not None
        has_activity = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='activity_rollups'"
        ).fetchone() is not None
        projection_status = "UNAVAILABLE"
        if has_rollups and has_activity:
            expected_rollups = [tuple(row) for row in conn.execute(
                "SELECT subject_ref, kind, pool_key, latest_capture_seq, row_count FROM ("
                "SELECT subject_ref, kind, COALESCE(resource_pool_ref, '') AS pool_key, capture_seq, "
                "FIRST_VALUE(capture_seq) OVER (PARTITION BY subject_ref, kind, COALESCE(resource_pool_ref, '') "
                "ORDER BY effective_at_us DESC, capture_seq DESC) AS latest_capture_seq, "
                "COUNT(*) OVER (PARTITION BY subject_ref, kind, COALESCE(resource_pool_ref, '')) AS row_count, "
                "ROW_NUMBER() OVER (PARTITION BY subject_ref, kind, COALESCE(resource_pool_ref, '') "
                "ORDER BY effective_at_us DESC, capture_seq DESC) AS rn FROM observations) WHERE rn=1 "
                "ORDER BY subject_ref, kind, pool_key"
            ).fetchall()]
            actual_rollups = [tuple(row) for row in conn.execute(
                "SELECT subject_ref, kind, pool_key, latest_capture_seq, row_count FROM observation_rollups "
                "ORDER BY subject_ref, kind, pool_key"
            ).fetchall()]
            expected_activity = [tuple(row) for row in conn.execute(
                "SELECT subject_ref, capture_seq FROM (SELECT subject_ref, capture_seq, "
                "ROW_NUMBER() OVER (PARTITION BY subject_ref ORDER BY capture_seq DESC) AS rn "
                "FROM observations WHERE kind='activity') WHERE rn <= ? ORDER BY subject_ref, capture_seq DESC",
                (ACTIVITY_WINDOW,),
            ).fetchall()]
            actual_activity = [tuple(row) for row in conn.execute(
                "SELECT subject_ref, capture_seq FROM activity_rollups ORDER BY subject_ref, capture_seq DESC"
            ).fetchall()]
            projection_status = "OK" if expected_rollups == actual_rollups and expected_activity == actual_activity else "ERROR"
        status = "OK" if rows == ["ok"] and projection_status != "ERROR" else "ERROR"
        return {"status": status, "sqlite": rows, "observation_projections": projection_status}
    finally:
        conn.close()


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
    def _open(self) -> sqlite3.Connection:
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
            # A bounded root-page probe preserves fail-closed behavior for damaged
            # authoritative tables without scanning every page on every dashboard frame.
            probe_table_roots(conn)
        except BaseException:
            try:
                conn.close()
            except Exception:
                pass
            raise
        return conn

    # ------------------------------------------------------------------ sections (all read from the same snapshot)
    @staticmethod
    def _peers(conn: sqlite3.Connection, _us: int) -> Section:
        rows = conn.execute("SELECT peer_id, display_name, adapter_ref, created_at FROM peers ORDER BY peer_id").fetchall()
        return Section("peers", "OK", {"peers": [dict(zip(("peer_id", "display_name", "adapter_ref", "created_at"), tuple(r))) for r in rows]})

    @staticmethod
    def _streams(conn: sqlite3.Connection, _us: int) -> Section:
        import json
        has_bridge_evidence = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bridge_evidence'"
        ).fetchone() is not None
        streams = conn.execute("SELECT stream_id, title, state, revision FROM streams ORDER BY stream_id").fetchall()
        ids = [s[0] for s in streams]
        if not ids:
            return Section("streams", "OK", {"streams": []})
        records = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT stream_id, COUNT(*), COALESCE(MAX(position), 0) FROM records GROUP BY stream_id"
        ).fetchall()}
        members: dict[str, list[str]] = {sid: [] for sid in ids}
        for sid, peer in conn.execute("SELECT stream_id, peer_id FROM stream_members ORDER BY stream_id, peer_id").fetchall():
            members[sid].append(peer)
        offsets: dict[str, dict[str, int]] = {sid: {} for sid in ids}
        for sid, peer, position in conn.execute("SELECT stream_id, peer_id, read_through_position FROM offsets ORDER BY stream_id, peer_id").fetchall():
            offsets[sid][peer] = position
        refusals: dict[str, str] = {}
        if has_bridge_evidence:
            for sid, raw in conn.execute(
                "SELECT stream_id, detail FROM (SELECT stream_id, detail, ROW_NUMBER() OVER (PARTITION BY stream_id ORDER BY seq DESC) AS rn "
                "FROM bridge_evidence WHERE kind = 'refusal_detected') WHERE rn = 1"
            ).fetchall():
                refusals[sid] = raw
        out: list[dict[str, Any]] = []
        for s in streams:
            sid = s[0]
            count, head = records.get(sid, (0, 0))
            stream_info: dict[str, Any] = {"stream_id": sid, "title": s[1], "state": s[2], "revision": s[3], "head_position": head,
                        "record_count": count, "members": members[sid], "offsets": offsets[sid]}
            if has_bridge_evidence:
                refusal_raw = refusals.get(sid)
                if refusal_raw:
                    try:
                        ref_detail = json.loads(refusal_raw)
                        ref_info: dict[str, Any] = {
                            "reason": ref_detail.get("reason"),
                            "task_outcome": ref_detail.get("task_outcome", "NOT_EXECUTED"),
                        }
                        if "reset_time" in ref_detail:
                            ref_info["reset_time"] = ref_detail["reset_time"]
                        elif "resets_at" in ref_detail:
                            ref_info["reset_time"] = ref_detail["resets_at"]
                        if "message" in ref_detail:
                            ref_info["message"] = ref_detail["message"]
                        stream_info["last_refusal"] = ref_info
                    except (ValueError, TypeError):
                        pass
            out.append(stream_info)
        return Section("streams", "OK", {"streams": out})

    @staticmethod
    def _pools(conn: sqlite3.Connection, _us: int) -> Section:
        has_rollups = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='observation_rollups'"
        ).fetchone() is not None
        if has_rollups:
            counts = {r[0]: r[1] for r in conn.execute(
                "SELECT pool_key, SUM(row_count) FROM observation_rollups WHERE pool_key != '' GROUP BY pool_key"
            ).fetchall()}
        else:
            counts = {r[0]: r[1] for r in conn.execute(
                "SELECT resource_pool_ref, COUNT(*) FROM observations WHERE resource_pool_ref IS NOT NULL GROUP BY resource_pool_ref"
            ).fetchall()}
        rows = conn.execute("SELECT resource_pool_id, provider, kind, metadata_json FROM resource_pools ORDER BY resource_pool_id").fetchall()
        return Section("resource_pools", "OK", {"pools": [
            {"resource_pool_id": r[0], "provider": r[1], "kind": r[2], "metadata_json": r[3], "observation_count": counts.get(r[0], 0)} for r in rows]})

    def _observations(self, conn: sqlite3.Connection, read_us: int) -> Section:
        has_rollups = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='observation_rollups'"
        ).fetchone() is not None
        if has_rollups:
            rows = conn.execute(
                "SELECT o.capture_seq, o.observation_id, o.subject_ref, o.resource_pool_ref, o.kind, o.source, "
                "o.state, o.payload_json, o.observed_at, o.captured_at, o.effective_at_us FROM observation_rollups AS h "
                "JOIN observations AS o ON o.capture_seq = h.latest_capture_seq "
                "ORDER BY h.subject_ref, h.kind, h.pool_key"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT capture_seq, observation_id, subject_ref, resource_pool_ref, kind, source, state, payload_json, "
                "observed_at, captured_at, effective_at_us FROM observations ORDER BY capture_seq"
            ).fetchall()
        best: dict[tuple[str, str, str | None], ObservationView] = {}
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
        items: list[dict[str, Any]] = []
        for key in sorted(best, key=lambda k: (k[0], k[1], k[2] or "")):
            v = best[key]
            o = v.observation
            items.append({"observation_id": o.observation_id, "subject_ref": o.subject_ref, "kind": o.kind, "resource_pool_ref": o.resource_pool_ref,
                          "source": o.source, "stored_state": v.stored_state.value, "state": v.state.value, "age_seconds": v.age_seconds,
                          "ttl_seconds": v.ttl_seconds, "basis": v.basis, "skew_seconds": v.skew_seconds, "capture_seq": v.capture_seq,
                          "observed_at": o.observed_at, "captured_at": o.captured_at, "payload": o.payload})
        return Section("observations", "ERROR" if errors else "OK", {"items": items}, tuple(errors))

    @staticmethod
    def _newer(a: ObservationView, b: ObservationView) -> bool:
        """TD-13/TD-23 ordering: effective capture time, then persisted capture ordinal."""
        ka, kb = (effective_us(a.observation), a.capture_seq), (effective_us(b.observation), b.capture_seq)
        return ka > kb

    @staticmethod
    def _activity(conn: sqlite3.Connection, read_us: int) -> Section:
        """Per peer, from the newest ACTIVITY_WINDOW `ask` activity observations: outcome counts, median duration, last ask.
        Outcomes are mutually exclusive: ok (delivered/recovered_terminal), uncertain, failed (everything else)."""
        import json
        import statistics
        from datetime import datetime

        has_activity_rollups = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='activity_rollups'"
        ).fetchone() is not None
        if has_activity_rollups:
            rows = conn.execute(
                "SELECT o.subject_ref, o.payload_json, o.observed_at FROM activity_rollups AS h "
                "JOIN observations AS o ON o.capture_seq=h.capture_seq "
                "ORDER BY h.subject_ref, h.capture_seq DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "WITH ranked AS (SELECT subject_ref, payload_json, observed_at, capture_seq, "
                "ROW_NUMBER() OVER (PARTITION BY subject_ref ORDER BY capture_seq DESC) AS rn "
                "FROM observations WHERE kind = 'activity') "
                "SELECT subject_ref, payload_json, observed_at FROM ranked WHERE rn <= ? ORDER BY subject_ref, rn",
                (ACTIVITY_WINDOW,),
            ).fetchall()
        per: dict[str, list[tuple[str, float | None, float | None]]] = {}
        for subject, raw, observed in rows:
            bucket = per.setdefault(subject, [])
            if len(bucket) >= ACTIVITY_WINDOW:
                continue
            payload: dict[str, Any] = {}
            try:
                parsed: object = json.loads(raw) if raw else {}
                if isinstance(parsed, dict):
                    payload = cast(dict[str, Any], parsed)
            except ValueError:
                pass
            took = payload.get("operation_elapsed_seconds")
            try:
                at: float | None = datetime.fromisoformat(str(observed)).timestamp()
            except ValueError:
                at = None
            bucket.append((str(payload.get("status") or "unknown"), float(took) if isinstance(took, (int, float)) and not isinstance(took, bool) else None, at))
        peers: list[dict[str, Any]] = []
        for peer in sorted(per):
            asks = per[peer]
            ok = sum(1 for st, _, _ in asks if st in ASK_OK_STATUSES)
            unc = sum(1 for st, _, _ in asks if st == "uncertain")
            durations = [d for _, d, _ in asks if d is not None]
            last_status, last_seconds, last_at = asks[0]
            peers.append({"peer": peer, "asks": len(asks), "ok": ok, "uncertain": unc, "failed": len(asks) - ok - unc,
                          "median_seconds": statistics.median(durations) if durations else None, "last_status": last_status,
                          "last_seconds": last_seconds,
                          "last_age_seconds": None if last_at is None else max(0.0, read_us / 1_000_000 - last_at)})
        return Section("activity", "OK", {"window": ACTIVITY_WINDOW, "peers": peers})

    def _log(self, _conn: sqlite3.Connection, _us: int) -> Section:
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
    def readiness(self, profile_ids: list[str], *, read_at: float | None = None) -> dict[str, Any]:
        """Read quota and the latest deliveries in one read-only transaction."""
        import json

        now = time.time() if read_at is None else read_at
        out: dict[str, Any] = dict(schema_version="1.0", read_at=now, status="OK", basis="cached",
                                   observations=[], deliveries=[], complete=False, error=None)
        if not Path(self.db_path).is_file():
            return dict(out, status="UNAVAILABLE", error=f"database not found: {self.db_path}")
        conn = None
        try:
            conn = self._open()
            obs = self._observations(conn, round(now * 1_000_000))
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            complete = obs.status == "OK" and {"bridge_deliveries", "bridge_evidence"} <= tables
            rows = []
            if {"bridge_deliveries", "bridge_evidence"} <= tables:
                # One uncapped batch, independent of monitor's bounded session sample.
                # Include corrupt metadata so it cannot silently certify older success.
                rows = [dict(r) for r in conn.execute(
                    "SELECT d.delivery_id,d.stream_id,d.certainty,d.result_json,d.terminal_at,"
                    "r.metadata_json,r.appended_at,e.kind,e.detail FROM bridge_deliveries d "
                    "JOIN records r ON r.record_id=d.record_id "
                    "LEFT JOIN bridge_evidence e ON e.seq=(SELECT MAX(x.seq) FROM bridge_evidence x "
                    "WHERE x.delivery_id=d.delivery_id AND x.kind IN "
                    "('terminal','refusal_detected','runtime_error','runtime_timeout','runtime_disconnect','start_uncertain')) "
                    "WHERE NOT json_valid(r.metadata_json) OR EXISTS "
                    "(SELECT 1 FROM json_each(?) p WHERE json_extract(CASE WHEN json_valid(r.metadata_json) "
                    "THEN r.metadata_json ELSE '{}' END,'$.binding') LIKE p.value || '/%') "
                    "ORDER BY d.seq DESC,d.attempt DESC,d.delivery_id DESC",
                    (json.dumps(profile_ids),))]
            return dict(out, observations=obs.data.get("items", []), deliveries=rows, complete=complete)
        except SchemaVersionError as exc:
            return dict(out, status="SCHEMA_VERSION", error=f"{type(exc).__name__}: {exc}")
        except (sqlite3.Error, OSError, DiagReadOnlyError, ValueError) as exc:
            return dict(out, status="FAILED", error=f"{type(exc).__name__}: {exc}")
        finally:
            if conn is not None:
                conn.close()

    def render(self, sections: list[str] | tuple[str, ...] | None = None, read_at: float | None = None) -> DiagnosticReport:
        names = list(SECTION_ORDER) if sections is None else list(sections)
        unknown = [n for n in names if n not in SECTION_ORDER and n not in OPTIONAL_SECTIONS]
        if unknown:
            raise ValueError(f"unknown diag section(s): {unknown}")
        now = time.time() if read_at is None else read_at
        read_us = round(now * 1_000_000)
        try:
            conn = self._open()
        except (sqlite3.Error, OSError, DiagReadOnlyError, SchemaVersionError) as e:
            return DiagnosticReport("FAILED", now, {}, {}, f"{type(e).__name__}: {e}")
        impl: dict[str, Callable[[sqlite3.Connection, int], Section]] = {"peers": self._peers, "streams": self._streams, "resource_pools": self._pools, "observations": self._observations, "activity": self._activity, "log": self._log}
        out: dict[str, Section] = {}
        try:
            snapshot: dict[str, Any] = {}
            def observation_total() -> int:
                has_rollups = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='observation_rollups'"
                ).fetchone() is not None
                if has_rollups:
                    return int(conn.execute("SELECT COALESCE(SUM(row_count), 0) FROM observation_rollups").fetchone()[0])
                return int(conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0])

            for key, one in (("records_total", lambda: conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]),
                             ("observations_total", observation_total)):
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
