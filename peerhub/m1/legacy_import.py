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
from typing import Any, Callable, cast

from .models import AppendRequest, canonical_json_bytes, compute_record_digest
from .schema_version import SUPPORTED_SCHEMA_VERSION, SchemaVersionError, future_schema_message
from .store import CoreStore

AUTHOR_ID = "legacy:orchestrator"
MAPPINGS = ["event_log->records", "consumer_offsets->offsets"]
_MAPPED_TABLES = {"event_log", "consumer_offsets"}
_EVENT_COLUMNS = ("outbox_position", "event_id", "protocol_major", "protocol_minor", "schema_version", "correlation_id", "occurred_at",
                  "event_kind", "payload_json", "request_id", "round_id", "evidence_refs_json", "predecessor_digest",
                  "recovery_context_json", "appended_at")


def EMPTY_PLAN() -> dict[str, list[Any]]:
    return {"write": [], "already": [], "conflicts": []}


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

    def __init__(self, status: str, reason: str, plan: dict[str, list[Any]]) -> None:
        super().__init__(status)
        self.status, self.reason, self.plan = status, reason, plan


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
            with closing(sqlite3.connect(_ro_uri(src, True), uri=True, timeout=30.0)) as c:
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
                unmapped: dict[str, int] = {}
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
                body: dict[str, Any] = {"event_id": eid, "event_kind": r["event_kind"], "correlation_id": cid, "occurred_at": r["occurred_at"],
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
            offsets_out: list[list[Any]] = []
            u["offsets"] = offsets_out
            if u["status"] != "malformed":
                for cons, pos in sorted(good_consumers):
                    n = sum(1 for p in u["positions"] if p <= pos)
                    if n:
                        offsets_out.append([cons, n])
        plan: dict[str, Any] = {"units": units, "unmapped": unmapped, "bad_consumers": bad_consumers}
        plan["plan_digest"] = _digest({
            "units": [{"unit": u["unit"], "malformed": u["status"] == "malformed", "rows": u["rows"], "digests": u["digests"],
                       "offsets": u["offsets"]} for u in units],
            "unmapped": unmapped, "bad_consumers": bad_consumers, "mappings": MAPPINGS})
        return plan

    # ------------------------------------------------------------------ classification against PERSISTED target rows
    @staticmethod
    def _peer_owned(conn: sqlite3.Connection, peer_id: str) -> bool | None:
        """None = absent; True only when the persisted marker EXACTLY equals the importer's marker for this peer (never null/partial)."""
        row = conn.execute("SELECT metadata_json FROM peers WHERE peer_id = ?", (peer_id,)).fetchone()
        if row is None:
            return None
        source = "event_log" if peer_id == AUTHOR_ID else "consumer_offsets"
        try:
            meta: object = json.loads(row[0])
            return isinstance(meta, dict) and cast("dict[str, Any]", meta).get("legacy_import") == {"source": source}
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _persisted_digests(conn: sqlite3.Connection, stream_id: str, limit: int) -> list[tuple[Any, ...]]:
        """(position, idempotency_key, digest RECOMPUTED from the persisted fields, author) -- never the stored payload_digest."""
        out: list[tuple[Any, ...]] = []
        for r in conn.execute("SELECT position, idempotency_key, stream_id, author_peer_id, kind, body_json, targets_json, reply_to, "
                              "refs_json, metadata_json, created_at, payload_digest FROM records WHERE stream_id = ? "
                              "ORDER BY position LIMIT ?", (stream_id, limit)):
            try:
                d = compute_record_digest({"stream_id": r[2], "author_peer_id": r[3], "kind": r[4],
                                           "body": None if r[5] is None else json.loads(r[5]), "targets": json.loads(r[6]),
                                           "reply_to": r[7], "refs": json.loads(r[8]), "metadata": json.loads(r[9]), "created_at": r[10]})
            except (ValueError, TypeError, KeyError):
                d = "unverifiable"
            out.append((r[0], r[1], d if d == r[11] else f"stored-mismatch:{d}", r[3]))
        return out

    def _offset_plan(self, conn: sqlite3.Connection | None, u: dict[str, Any], *, fresh: bool) -> dict[str, list[Any]]:
        """Per-component (offsets) accounting against PERSISTED rows. Never modifies an existing offset (TD-10: no guessing):
        a differing M1 offset is reported as `m1_ahead` (M1 moved past the legacy value) or `legacy_ahead` (legacy moved on)."""
        plan: dict[str, list[Any]] = {"write": [], "already": [], "conflicts": []}
        for cons, n in u["offsets"]:
            pid = f"legacy:consumer:{cons}"
            if fresh or conn is None:
                plan["write"].append([cons, n])
                continue
            if self._peer_owned(conn, pid) is False:
                plan["conflicts"].append({"consumer": cons, "kind": "foreign_peer", "legacy": n, "m1": None})
                continue
            row = conn.execute("SELECT read_through_position FROM offsets WHERE peer_id = ? AND stream_id = ?", (pid, u["unit"])).fetchone()
            if row is None:
                plan["write"].append([cons, n])
            elif row[0] == n:
                plan["already"].append([cons, n])
            else:
                plan["conflicts"].append({"consumer": cons, "kind": "m1_ahead" if row[0] > n else "legacy_ahead", "legacy": n, "m1": row[0]})
        return plan

    def _classify(self, conn: sqlite3.Connection | None, u: dict[str, Any]) -> tuple[str, str, dict[str, list[Any]]]:
        if u["status"] == "malformed":
            return "malformed", u["reason"], EMPTY_PLAN()
        if conn is None:
            return "new", "", self._offset_plan(None, u, fresh=True)
        needed = [AUTHOR_ID] + [f"legacy:consumer:{c}" for c, _ in u["offsets"]]
        srow = conn.execute("SELECT metadata_json FROM streams WHERE stream_id = ?", (u["unit"],)).fetchone()
        if srow is None:
            for pid in needed:
                if self._peer_owned(conn, pid) is False:
                    return "conflict", f"peer {pid!r} exists and was not created by the legacy importer; refusing to modify it", EMPTY_PLAN()
            return "new", "", self._offset_plan(conn, u, fresh=True)
        try:
            marker: dict[str, Any] = json.loads(srow[0]).get("legacy_import", {})
        except (ValueError, TypeError, AttributeError):
            marker = {}
        if marker.get("source") != "event_log" or marker.get("group") != u["cid"]:
            return "conflict", f"stream {u['unit']!r} exists and was not created by the legacy importer; refusing to modify it", EMPTY_PLAN()
        have = self._persisted_digests(conn, u["unit"], len(u["digests"]))
        want = [(i + 1, r["idempotency_key"], d, AUTHOR_ID) for i, (r, d) in enumerate(zip(u["requests"], u["digests"]))]
        if [tuple(h) for h in have] != want:
            return "conflict", "imported records differ from the legacy source (changed legacy row or divergent target); not overwritten", EMPTY_PLAN()
        if self._peer_owned(conn, AUTHOR_ID) is False:
            return "conflict", f"peer {AUTHOR_ID!r} exists and was not created by the legacy importer; refusing to modify it", EMPTY_PLAN()
        oplan = self._offset_plan(conn, u, fresh=False)
        return ("update" if oplan["write"] else "already_imported"), "", oplan

    def _open_target_readonly(self) -> sqlite3.Connection | None:
        if not self.target.exists() or self.target.stat().st_size == 0:
            return None
        try:
            # same bounded wait as CoreStore: concurrent importers on slow runners hit "database is locked" with the 5 s default
            conn = sqlite3.connect(_ro_uri(self.target, not _has_pending_wal(self.target)), uri=True, timeout=30.0)
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
                "would_import_records": 0, "peers_created": 0, "offsets_written": 0, "updated_units": 0, "would_write_offsets": 0,
                "offset_conflicts": 0}

    def dry_run(self) -> dict[str, Any]:
        plan = self._plan()
        conn = self._open_target_readonly()
        results: list[dict[str, Any]] = []
        totals = self._zero()
        try:
            for u in plan["units"]:
                status, reason, oplan = self._classify(conn, u)
                results.append({"unit": u["unit"], "status": status, "records": len(u["digests"]), "reason": reason, "rows": u["rows"],
                                "offsets": oplan})
                key = {"new": None, "update": "updated_units", "already_imported": "already_imported_units", "conflict": "conflict_units",
                       "malformed": "malformed_units"}[status]
                if key:
                    totals[key] += 1
                if status == "new":
                    totals["would_import_records"] += len(u["digests"])
                totals["would_write_offsets"] += len(oplan["write"])
                totals["offset_conflicts"] += len(oplan["conflicts"])
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
        results: list[dict[str, Any]] = []
        totals = self._zero()
        for u in plan["units"]:
            entry = {"unit": u["unit"], "status": "malformed", "records": len(u["digests"]), "reason": u["reason"], "rows": u["rows"],
                     "offsets": EMPTY_PLAN()}
            results.append(entry)
            if u["status"] == "malformed":
                totals["malformed_units"] += 1
                continue
            try:
                with store.transaction("import.unit") as conn:  # one atomic unit: classify against persisted rows, then write
                    status, reason, oplan = self._classify(conn, u)
                    if status == "new":
                        peers, offsets = self._write_unit(store, conn, u)
                    elif status == "update":
                        peers, offsets = self._write_offsets(conn, u, oplan["write"]), len(oplan["write"])
                    else:
                        raise _NoWrite(status, reason, oplan)
            except _NoWrite as nw:
                entry.update(status=nw.status, reason=nw.reason, offsets=nw.plan)
                totals["already_imported_units" if nw.status == "already_imported" else "conflict_units"] += 1
                totals["offset_conflicts"] += len(nw.plan["conflicts"])
                continue
            store.fire("import.unit.after_commit")  # committed; the report has not been produced yet (crash seam)
            entry.update(status="imported" if status == "new" else "updated", reason="", offsets=oplan)
            totals["imported_units" if status == "new" else "updated_units"] += 1
            if status == "new":
                totals["records_imported"] += len(u["digests"])
            totals["peers_created"] += peers
            totals["offsets_written"] += offsets
            totals["offset_conflicts"] += len(oplan["conflicts"])
        return self._report("apply", plan, results, totals, self.source)

    def _write_offsets(self, conn: sqlite3.Connection, u: dict[str, Any], todo: list[list[Any]]) -> int:
        """Add ONLY missing consumer components to an already imported unit: peers, stream membership, offsets. Returns peers created."""
        created, made = u["created_at"], 0
        for cons, n in todo:
            pid = f"legacy:consumer:{cons}"
            if conn.execute("SELECT 1 FROM peers WHERE peer_id = ?", (pid,)).fetchone() is None:
                conn.execute("INSERT INTO peers (peer_id, display_name, adapter_ref, metadata_json, created_at) VALUES (?, NULL, NULL, ?, ?)",
                             (pid, json.dumps({"legacy_import": {"source": "consumer_offsets"}}), created))
                made += 1
            if conn.execute("SELECT 1 FROM stream_members WHERE stream_id = ? AND peer_id = ?", (u["unit"], pid)).fetchone() is None:
                conn.execute("INSERT INTO stream_members (stream_id, peer_id) VALUES (?, ?)", (u["unit"], pid))
            conn.execute("INSERT INTO offsets (peer_id, stream_id, read_through_position, revision) VALUES (?, ?, ?, 2)", (pid, u["unit"], n))
        return made

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
            store.insert_record(conn, AppendRequest(**request), digest)
        for cons, n in u["offsets"]:
            conn.execute("INSERT INTO offsets (peer_id, stream_id, read_through_position, revision) VALUES (?, ?, ?, 2)",
                         (f"legacy:consumer:{cons}", u["unit"], n))
        return peers_created, len(u["offsets"])
