"""Explicit side-by-side importer of a legacy v0.x PeerHub store into the M1 Core (TD-16, MIGRATION_CUTOVER step 7).

Declared mappings (everything else is REPORTED as unmapped, never guessed; D-W7-1):
  event_log        -> Records of Stream `legacy:<correlation_id>` authored by Peer `legacy:orchestrator`
                      (kind `legacy.event`, body = every event_log column, JSON columns parsed; created_at = occurred_at epoch seconds)
  consumer_offsets -> Peer `legacy:consumer:<consumer_id>` + one Offset per Stream = number of that Stream's imported Records at or
                      before the consumer's legacy outbox position

Guarantees:
  * the legacy database is opened read-only/immutable and never written; a dry-run writes nothing anywhere (the target is not even created);
  * atomicity unit = one correlation group (Stream + its Records + needed Peers + Offsets) in ONE transaction: a crash leaves only whole
    units; re-running resumes with exact accounting;
  * idempotent: a unit is `already_imported` only if the PERSISTED rows (stream marker, record keys and digests, in order) match the
    legacy source; M1 Records appended later are tolerated (prefix match). Foreign or divergent rows are reported as `conflict` and never touched;
  * a malformed legacy row isolates its whole group (`malformed`), the other groups still import.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .models import AppendRequest, canonical_json_bytes, compute_record_digest
from .schema_version import SUPPORTED_SCHEMA_VERSION, SchemaVersionError, future_schema_message
from .store import CoreStore

AUTHOR_ID = "legacy:orchestrator"
MAPPINGS = ["event_log->records", "consumer_offsets->offsets"]
_MAPPED_TABLES = {"event_log", "consumer_offsets"}
_EVENT_COLUMNS = ("outbox_position", "event_id", "protocol_major", "protocol_minor", "schema_version", "correlation_id", "occurred_at",
                  "event_kind", "payload_json", "request_id", "round_id", "evidence_refs_json", "predecessor_digest",
                  "recovery_context_json", "appended_at")


class LegacySourceError(RuntimeError):
    """The legacy source (or the M1 target) cannot be used safely; nothing was written."""


class LegacyPlanChangedError(RuntimeError):
    """The legacy source changed since the plan the caller approved; nothing was written."""


class _Malformed(ValueError):
    def __init__(self, reason: str, row: int | None) -> None:
        super().__init__(reason)
        self.reason, self.row = reason, row


class _NoWrite(Exception):
    """Internal: unit needs no write (already imported / conflict); rolls back the empty transaction."""

    def __init__(self, status: str, reason: str = "") -> None:
        super().__init__(status)
        self.status, self.reason = status, reason


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite JSON constant {name}")


def _loads(text: Any, what: str, row: int) -> Any:
    if not isinstance(text, str):
        raise _Malformed(f"{what} is not text", row)
    try:
        return json.loads(text, parse_constant=_reject_constant)
    except ValueError as e:
        raise _Malformed(f"{what} is not valid JSON ({e})", row) from None


def _iso(seconds: Any, what: str, row: int) -> str:
    if type(seconds) is not int or seconds < 0:
        raise _Malformed(f"{what} is not a non-negative integer", row)
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, ValueError, OSError):
        raise _Malformed(f"{what} is out of range", row) from None


def _opt_text(v: Any, what: str, row: int) -> str | None:
    if v is not None and not isinstance(v, str):
        raise _Malformed(f"{what} is not text", row)
    return v


def _digest(obj: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def _ro_uri(path: Path, immutable: bool) -> str:
    return path.resolve().as_uri() + "?mode=ro" + ("&immutable=1" if immutable else "")


def _has_pending_wal(path: Path) -> bool:
    wal = Path(str(path) + "-wal")
    return wal.exists() and wal.stat().st_size > 0


class LegacyImporter:
    def __init__(self, source: str | Path, target: str | Path, fault_hook: Callable[[str], None] | None = None) -> None:
        self.source, self.target, self.fault_hook = Path(source), Path(target), fault_hook

    # ------------------------------------------------------------------ source -> plan
    def _read_source(self) -> tuple[list[sqlite3.Row], list[sqlite3.Row], dict[str, int]]:
        src = self.source
        if not src.is_file():
            raise LegacySourceError(f"legacy source {str(src)!r} does not exist")
        if self.target.exists() and src.samefile(self.target):
            raise LegacySourceError("legacy source and M1 target are the same file")
        if _has_pending_wal(src):
            raise LegacySourceError("legacy source has an un-checkpointed -wal file: close the legacy runtime / checkpoint it first "
                                    "(the importer never writes to the source)")
        try:
            with closing(sqlite3.connect(_ro_uri(src, True), uri=True)) as c:
                c.row_factory = sqlite3.Row
                c.execute("BEGIN")
                tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                if "event_log" not in tables:
                    raise LegacySourceError("not a legacy v0.x PeerHub store: table event_log is missing")
                cols = {r[1] for r in c.execute("PRAGMA table_info(event_log)")}
                if not set(_EVENT_COLUMNS) <= cols:
                    raise LegacySourceError(f"event_log lacks expected columns: {sorted(set(_EVENT_COLUMNS) - cols)}")
                events = c.execute(f"SELECT {', '.join(_EVENT_COLUMNS)} FROM event_log ORDER BY outbox_position").fetchall()
                consumers = (c.execute("SELECT consumer_id, outbox_position FROM consumer_offsets ORDER BY consumer_id").fetchall()
                             if "consumer_offsets" in tables else [])
                unmapped = {}
                for t in sorted(tables - _MAPPED_TABLES):
                    n = c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                    if n:
                        unmapped[t] = n
                return events, consumers, unmapped
        except sqlite3.DatabaseError as e:
            raise LegacySourceError(f"legacy source is not a readable SQLite database: {e}") from e

    @staticmethod
    def _build_unit(cid: Any, rows: list[sqlite3.Row]) -> dict[str, Any]:
        unit: dict[str, Any] = {"unit": f"legacy:{cid}", "cid": cid, "status": None, "reason": "", "rows": [], "events": [], "requests": [],
                                "digests": [], "positions": [], "created_at": None}
        bad: list[tuple[int, str]] = []
        for r in rows:
            pos = r["outbox_position"]
            try:
                if not isinstance(cid, str) or not cid:
                    raise _Malformed("correlation_id is empty or not text", pos)
                eid = r["event_id"]
                if not isinstance(eid, str) or not eid:
                    raise _Malformed("event_id is empty or not text", pos)
                if not isinstance(r["event_kind"], str) or not r["event_kind"]:
                    raise _Malformed("event_kind is empty or not text", pos)
                if type(r["protocol_major"]) is not int or type(r["protocol_minor"]) is not int or not isinstance(r["schema_version"], str):
                    raise _Malformed("protocol/schema fields are malformed", pos)
                occurred = _iso(r["occurred_at"], "occurred_at", pos)
                _iso(r["appended_at"], "appended_at", pos)
                payload = _loads(r["payload_json"], "payload_json", pos)
                refs = _loads(r["evidence_refs_json"], "evidence_refs_json", pos)
                if not isinstance(refs, list):
                    raise _Malformed("evidence_refs_json is not a JSON list", pos)
                recovery = None if r["recovery_context_json"] is None else _loads(r["recovery_context_json"], "recovery_context_json", pos)
                body = {"event_id": eid, "event_kind": r["event_kind"], "correlation_id": cid, "occurred_at": r["occurred_at"],
                        "appended_at": r["appended_at"], "outbox_position": pos, "protocol_major": r["protocol_major"],
                        "protocol_minor": r["protocol_minor"], "schema_version": r["schema_version"],
                        "request_id": _opt_text(r["request_id"], "request_id", pos), "round_id": _opt_text(r["round_id"], "round_id", pos),
                        "evidence_refs": refs, "predecessor_digest": _opt_text(r["predecessor_digest"], "predecessor_digest", pos),
                        "recovery_context": recovery, "payload": payload}
                try:
                    req = AppendRequest(stream_id=f"legacy:{cid}", author_peer_id=AUTHOR_ID, kind="legacy.event", body=body,
                                        idempotency_key=f"legacy:event:{eid}", created_at=occurred,
                                        metadata={"legacy_import": {"source": "event_log", "outbox_position": pos}})
                except ValueError as e:
                    raise _Malformed(f"not representable as a Record ({str(e).splitlines()[0]})", pos) from None
                unit["requests"].append(req.model_dump())
                unit["digests"].append(compute_record_digest(req.model_dump()))
                unit["positions"].append(pos)
                if unit["created_at"] is None:
                    unit["created_at"] = occurred
            except _Malformed as m:
                bad.append((pos, m.reason))
        if bad:
            unit.update(status="malformed", rows=[p for p, _ in bad], reason="; ".join(f"row {p}: {why}" for p, why in bad),
                        requests=[], digests=[], positions=[])
        return unit

    def _plan(self) -> dict[str, Any]:
        events, consumers, unmapped = self._read_source()
        groups: dict[Any, list[sqlite3.Row]] = {}
        for r in events:
            groups.setdefault(r["correlation_id"], []).append(r)
        units = [self._build_unit(cid, rows) for cid, rows in groups.items()]
        good_consumers: list[tuple[str, int]] = []
        bad_consumers: list[dict[str, Any]] = []
        for c in consumers:
            cid_, pos = c["consumer_id"], c["outbox_position"]
            if isinstance(cid_, str) and cid_ and type(pos) is int and pos >= 1:
                good_consumers.append((cid_, pos))
            else:
                bad_consumers.append({"consumer_id": cid_, "outbox_position": pos, "reason": "malformed consumer_offsets row"})
        for u in units:
            u["offsets"] = []
            if u["status"] != "malformed":
                for cons, pos in sorted(good_consumers):
                    n = sum(1 for p in u["positions"] if p <= pos)
                    if n:
                        u["offsets"].append([cons, n])
        plan = {"units": units, "unmapped": unmapped, "bad_consumers": bad_consumers}
        plan["plan_digest"] = _digest({
            "units": [{"unit": u["unit"], "malformed": u["status"] == "malformed", "rows": u["rows"], "digests": u["digests"],
                       "offsets": u["offsets"]} for u in units],
            "unmapped": unmapped, "bad_consumers": bad_consumers, "mappings": MAPPINGS})
        return plan

    # ------------------------------------------------------------------ classification against PERSISTED target rows
    @staticmethod
    def _peer_owned(conn: sqlite3.Connection, peer_id: str) -> bool | None:
        row = conn.execute("SELECT metadata_json FROM peers WHERE peer_id = ?", (peer_id,)).fetchone()
        if row is None:
            return None
        try:
            return "legacy_import" in json.loads(row[0])
        except (ValueError, TypeError):
            return False

    def _classify(self, conn: sqlite3.Connection | None, u: dict[str, Any]) -> tuple[str, str]:
        if u["status"] == "malformed":
            return "malformed", u["reason"]
        if conn is None:
            return "new", ""
        needed = [AUTHOR_ID] + [f"legacy:consumer:{c}" for c, _ in u["offsets"]]
        srow = conn.execute("SELECT metadata_json FROM streams WHERE stream_id = ?", (u["unit"],)).fetchone()
        if srow is None:
            for pid in needed:
                if self._peer_owned(conn, pid) is False:
                    return "conflict", f"peer {pid!r} exists and was not created by the legacy importer; refusing to modify it"
            return "new", ""
        try:
            marker = json.loads(srow[0]).get("legacy_import", {})
        except (ValueError, TypeError, AttributeError):
            marker = {}
        if marker.get("source") != "event_log" or marker.get("group") != u["cid"]:
            return "conflict", f"stream {u['unit']!r} exists and was not created by the legacy importer; refusing to modify it"
        have = conn.execute("SELECT position, idempotency_key, payload_digest, author_peer_id FROM records WHERE stream_id = ? "
                            "ORDER BY position LIMIT ?", (u["unit"], len(u["digests"]))).fetchall()
        want = [(i + 1, r["idempotency_key"], d, AUTHOR_ID) for i, (r, d) in enumerate(zip(u["requests"], u["digests"]))]
        if [tuple(h) for h in have] != want:
            return "conflict", "imported records differ from the legacy source (changed legacy row or divergent target); not overwritten"
        return "already_imported", ""

    def _open_target_readonly(self) -> sqlite3.Connection | None:
        if not self.target.exists() or self.target.stat().st_size == 0:
            return None
        try:
            conn = sqlite3.connect(_ro_uri(self.target, not _has_pending_wal(self.target)), uri=True)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SUPPORTED_SCHEMA_VERSION:
                conn.close()
                raise SchemaVersionError(future_schema_message(version, SUPPORTED_SCHEMA_VERSION))
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"peers", "streams", "records"} <= tables:
                conn.close()
                raise LegacySourceError("target exists but is not an M1 Core database; refusing to use it")
            return conn
        except sqlite3.DatabaseError as e:
            raise LegacySourceError(f"target is not a readable SQLite database: {e}") from e

    # ------------------------------------------------------------------ public API
    @staticmethod
    def _report(mode: str, plan: dict[str, Any], results: list[dict[str, Any]], totals: dict[str, int], source: Path) -> dict[str, Any]:
        return {"mode": mode, "source": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "plan_digest": plan["plan_digest"], "mappings": list(MAPPINGS), "units": results,
                "unmapped_tables": dict(plan["unmapped"]), "malformed_consumers": plan["bad_consumers"], "totals": totals}

    @staticmethod
    def _zero() -> dict[str, int]:
        return {"imported_units": 0, "already_imported_units": 0, "conflict_units": 0, "malformed_units": 0, "records_imported": 0,
                "would_import_records": 0, "peers_created": 0, "offsets_written": 0}

    def dry_run(self) -> dict[str, Any]:
        plan = self._plan()
        conn = self._open_target_readonly()
        results, totals = [], self._zero()
        try:
            for u in plan["units"]:
                status, reason = self._classify(conn, u)
                results.append({"unit": u["unit"], "status": status, "records": len(u["digests"]), "reason": reason, "rows": u["rows"]})
                key = {"new": None, "already_imported": "already_imported_units", "conflict": "conflict_units", "malformed": "malformed_units"}[status]
                if key:
                    totals[key] += 1
                else:
                    totals["would_import_records"] += len(u["digests"])
        finally:
            if conn is not None:
                conn.close()
        return self._report("dry-run", plan, results, totals, self.source)

    def apply(self, expected_plan_digest: str | None = None) -> dict[str, Any]:
        plan = self._plan()
        if expected_plan_digest is not None and expected_plan_digest != plan["plan_digest"]:
            raise LegacyPlanChangedError("the legacy source changed since the approved dry-run (plan digest differs); nothing was written")
        self.target.parent.mkdir(parents=True, exist_ok=True)  # only apply creates anything, and only after the source/plan checks passed
        store = CoreStore(self.target, fault_hook=self.fault_hook)  # creates/migrates the M1 target; refuses a future schema
        results, totals = [], self._zero()
        for u in plan["units"]:
            entry = {"unit": u["unit"], "status": "malformed", "records": len(u["digests"]), "reason": u["reason"], "rows": u["rows"]}
            results.append(entry)
            if u["status"] == "malformed":
                totals["malformed_units"] += 1
                continue
            try:
                with store.transaction("import.unit") as conn:  # one atomic unit: classify against persisted rows, then write
                    status, reason = self._classify(conn, u)
                    if status != "new":
                        raise _NoWrite(status, reason)
                    peers, offsets = self._write_unit(store, conn, u)
            except _NoWrite as nw:
                entry.update(status=nw.status, reason=nw.reason, records=len(u["digests"]))
                totals["already_imported_units" if nw.status == "already_imported" else "conflict_units"] += 1
                continue
            store._fire("import.unit.after_commit")  # noqa: SLF001 - committed; the report has not been produced yet (crash seam)
            entry.update(status="imported", reason="")
            totals["imported_units"] += 1
            totals["records_imported"] += len(u["digests"])
            totals["peers_created"] += peers
            totals["offsets_written"] += offsets
        return self._report("apply", plan, results, totals, self.source)

    def _write_unit(self, store: CoreStore, conn: sqlite3.Connection, u: dict[str, Any]) -> tuple[int, int]:
        created = u["created_at"]
        consumers = [c for c, _ in u["offsets"]]
        peers_created = 0
        for pid, src in [(AUTHOR_ID, "event_log")] + [(f"legacy:consumer:{c}", "consumer_offsets") for c in consumers]:
            if conn.execute("SELECT 1 FROM peers WHERE peer_id = ?", (pid,)).fetchone() is None:
                conn.execute("INSERT INTO peers (peer_id, display_name, adapter_ref, metadata_json, created_at) VALUES (?, NULL, NULL, ?, ?)",
                             (pid, json.dumps({"legacy_import": {"source": src}}), created))
                peers_created += 1
        conn.execute("INSERT INTO streams (stream_id, title, state, revision, metadata_json, created_at) VALUES (?, ?, 'OPEN', 1, ?, ?)",
                     (u["unit"], f"Legacy correlation {u['cid']}",
                      json.dumps({"legacy_import": {"source": "event_log", "group": u["cid"], "records": len(u["digests"])}}), created))
        for member in [AUTHOR_ID] + [f"legacy:consumer:{c}" for c in consumers]:  # rowid keeps declared order
            conn.execute("INSERT INTO stream_members (stream_id, peer_id) VALUES (?, ?)", (u["unit"], member))
        for request, digest in zip(u["requests"], u["digests"]):
            store._insert_record(conn, AppendRequest(**request), digest)  # noqa: SLF001 - same insert path as append_record
        for cons, n in u["offsets"]:
            conn.execute("INSERT INTO offsets (peer_id, stream_id, read_through_position, revision) VALUES (?, ?, ?, 2)",
                         (f"legacy:consumer:{cons}", u["unit"], n))
        return peers_created, len(u["offsets"])
