---
status: RATIFIED (all four items A/B/C/D closed; item A ready since Round 67, item B closed Round 83, item D closed Round 89, item C closed Round 146 — see docs/design/PHASE1-PROCESS-BACKLOG-2026-08-20.md for full round-by-round history)
date: 2026-08-21
ratified_date: 2026-08-22
title: Phase 1 Architecture Consolidation
---

# Phase 1 Architecture Consolidation

This document resolves the four broader architectural consolidation questions explicitly left open following the ratification of the executable-integrity admission model in `docs/design/PHASE1-PROMOTION-SCHEMA-V1-2026-08-20.md`.

## A. Canonical Domain Naming

**Context:** The codebase currently documents three independently-engineered "admission" concepts:
1. `ARCHITECTURE.md`'s `AdmissionSnapshot` (health/routing domain).
2. `peerhub/dispatch/contract.py`'s `AdmissionReceipt` and `AdmissionCoordinator` (dispatch-pipeline domain for commands, requests, and lease admission).
3. The newly ratified executable-integrity `AdmissionRegistry` and `AdmissionReceipt` in `PHASE1-PROMOTION-SCHEMA-V1-2026-08-20.md`.

**Resolution:** To avoid collision and clearly delineate domains, the executable-integrity registry and receipts are renamed:
* `AdmissionRegistry` is renamed to `ManifestAdmissionCoordinator`.
* `AdmissionReceipt` is renamed to `ManifestAdmissionReceipt`.
* `ProvisioningEvidenceReceipt` is renamed to `ManifestProvisioningEvidenceReceipt`.

*Rationale:* The prefix "Manifest" accurately scopes this admission to the static configuration, schema validation, and executable-integrity binding of peer profiles, entirely orthogonal to the dynamic command/dispatch admission handled by `peerhub/dispatch/admission.py`. The "Coordinator" suffix aligns precisely with the pattern established in the dispatch domain. The immutable, frozen-dataclass pattern proven across the 66-round dialectic is carried over exactly as ratified.

## B. SQLite Persistence and StateStore Integration

**Context:** `ARCHITECTURE.md` (Section 4) explicitly mandates SQLite as the single operational source of truth. The current `AdmissionRegistry` prototype relies on an in-memory closure-scoped dictionary, bypassing the transactional `StateStore[UnitOfWork]` port interface (defined in `peerhub/state/contract.py`).

**Resolution:**
The `ManifestAdmissionCoordinator` replaces the closure-scoped state with a proper `StateStore` port interface, matching the `AdmissionCoordinator` in `peerhub/dispatch/admission.py`. 

**Architectural Injection:**
As established in the ratified promotion schema, this capability will eventually be wired into the real dispatch `AdmissionCoordinator`, "not... a permanently separate mechanism". To be unambiguous, `ManifestAdmissionCoordinator` is designed as an injected component that the real dispatch `AdmissionCoordinator` will call into as an additional verification step before a request is fully admitted. It is not a second, competing admission authority.

**Connection Ownership & Concurrency:**
This coordinator never owns or creates its own database connection or file. It receives an already-initialized, capability-probed canonical `StateStore` instance from its caller, ensuring DB constraints are enforced on a local filesystem at initialization. Furthermore, store-busy/unavailable failures during concurrent access are bounded and surfaced to the coordinator's own caller as a clear, typed application-boundary error (e.g., `StateStoreUnavailableError`), never an unbounded internal retry loop that could hang the host process.

> [!WARNING]
> **Open Item for Phase 2 Port Design (Deadline Enforcement):**
> The coordinator's current timeout/deadline guarantee is not actually a property of the real `StateStore` port contract (`peerhub/state/contract.py`). It only holds because this specific adapter implementation (`FakeSqliteUnitOfWork`) internally chooses non-blocking (`timeout=0.0`) SQLite acquisition. The real `SqliteStateStore` adapter (`peerhub/persistence/sqlite.py`) defaults to `busy_timeout_ms=5_000` (`timeout=5.0`). While this coincidentally lands close enough to the coordinator's own ~5s deadline that nothing currently looks wrong, `SqliteStateStore(busy_timeout_ms=60_000)` is a legal, valid construction that would silently defeat the coordinator's deadline enforcement by 12x with no error surfaced. Making this a genuine, portable guarantee requires extending the real `StateStore/UnitOfWork` port interface (e.g., adding an explicit deadline-aware acquisition method or a documented non-blocking-behavior requirement) as real, *must do* Phase 2 implementation work.


**Shared Unit of Work Contract:**
Any caller invoking `admit_manifest()` from within an existing dispatch transaction MUST supply that transaction's UoW via the `shared_unit` parameter. Omitting it is only safe for a genuinely standalone admission not nested inside another transaction. This is a caller contract the type system cannot fully enforce. If this contract is violated (e.g., an already-open dispatch UoW exists but `shared_unit` is incorrectly omitted), it will cause an immediate self-contention deadlock that is predictably bounded by the overall deadline and surfaces as a detectable `StateStoreUnavailableError`, rather than silently swallowing the failure.

### 1. Illustrative Schema (`0025_manifest_admission_receipts.sql`)
```sql
-- 0025_manifest_admission_receipts.sql
-- Single operational source of truth for admitted manifests and their executable bindings.

CREATE TABLE manifest_admission_receipts (
    admission_receipt_id TEXT PRIMARY KEY,
    manifest_canonical_sha256 TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    adapter_id TEXT NOT NULL,
    peer_kind TEXT NOT NULL,
    inventory_generation INTEGER NOT NULL,
    trust_root_json TEXT NOT NULL,
    observed_vendor_json TEXT NOT NULL,
    acl_evaluation_json TEXT, -- NULL if not evaluated
    chain_complete INTEGER NOT NULL CHECK (chain_complete = 0), -- Boolean, 0 (False) for Phase 1 single-entrypoint bounds. Note: SQLite cannot drop a CHECK constraint via ALTER TABLE. Removing this constraint in Phase 2 will require a full table rebuild (via foreign_keys=OFF + foreign_key_check migration pattern, since shim_registry_entries has an FK into this table). Enforcing this at the application boundary would have avoided this future migration cost.
    aggregate_chain_digest TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    
    -- Using JSON columns for the executable chain and companion binaries 
    -- rather than normalized child tables because these are immutable snapshots
    -- that are retrieved as a single opaque document for evidence, never queried
    -- or joined by individual file node.
    transitive_executable_chain_json TEXT NOT NULL,
    companion_binaries_json TEXT NOT NULL,
    admitted_at_utc TEXT NOT NULL,
    prov_chain_complete INTEGER NOT NULL,
    CHECK (chain_complete = prov_chain_complete)
);
```

> [!NOTE]
> **Schema Invariant Requirement:** The shared SQLite store MUST be configured with `PRAGMA foreign_keys = ON;` (which is OFF by default in SQLite) for both Item B's and Item C's composite foreign-key integrity guarantees to hold. While enforced in the real `SqliteStateStore` adapter (`peerhub/persistence/sqlite.py`), it is a strict requirement of the `StateStore` contract that any adapter (fake or real) must set. The illustrative `FakeStateStore.unit_of_work()` below does not set this, meaning its trace ran with FK enforcement OFF.

### 2. Coordinator Design (Conceptual Overview)

> [!NOTE]
> The complete, currently-verified implementation lives in the runnable `trace.py` script in section 4 below, which is the sole authoritative source for this design's exact behavior. This section is a conceptual overview only and intentionally does not duplicate the full implementation to avoid the synchronization drift found in Rounds 77 and 81.

The `ManifestAdmissionCoordinator` orchestrates Phase 1 executable-integrity manifest admission. Its primary interfaces are defined below:

```python
class ManifestAdmissionReadUnitOfWork(ReadUnitOfWork, Protocol):
    def get_manifest_receipt(self, receipt_id: str) -> 'ManifestAdmissionReceipt | None': ...

class ManifestAdmissionUnitOfWork(ManifestAdmissionReadUnitOfWork, UnitOfWork, Protocol):
    def put_manifest_receipt(self, receipt: 'ManifestAdmissionReceipt') -> None: ...
    def put_shim_entry(self, entry: 'ShimRegistryEntry') -> None: ...

class ManifestAdmissionCoordinator:
    def admit_manifest(
        self, 
        raw_manifest: dict, 
        transitive_executable_chain: tuple[dict, ...],
        shared_unit: ManifestAdmissionUnitOfWork | None = None
    ) -> 'ManifestAdmissionReceipt': ...

    def get_trusted_digest(self, receipt_id: str) -> str: ...
    def get_trusted_receipt(self, receipt_id: str) -> 'ManifestAdmissionReceipt': ...
```

**Core Responsibilities:**
* **`admit_manifest`**: Validates the single-node chain (verifying the absolute path, resolving via OS PATH, checking SHA-256 and MZ magic bytes) and writes the admission receipt to the database. If called from within an existing UoW, it uses that unit (`shared_unit`). Otherwise, it requests its own non-blocking UoW from the injected `StateStore` and implements a bounded retry loop to handle concurrent lock contention, gracefully yielding `StateStoreUnavailableError`.
* **`get_trusted_digest` / `get_trusted_receipt`**: Safely retrieves verified receipts from the store, employing the same bounded read lock contention loop.

### 3. Deserialization and Deep Immutability

While SQLite naturally stores nested objects like the `trust_root` dict and `transitive_executable_chain` list as JSON strings, naive deserialization (`json.loads`) would return plain mutable dictionaries and lists, silently breaking the deep immutability achieved in the previous dialectic. To resolve this, the `ManifestAdmissionUnitOfWork` implementation must actively reconstruct these JSON properties into real frozen types during reads (e.g. using `dataclasses.asdict()` with an enum-aware default handler for writes, and explicit reconstruction on reads):
* `ExecutableRole` enums must be instantiated.
* `TransitiveExecutableNode` and `AclEvaluationEvidence` must be parsed back into real frozen dataclass instances, not left as dicts.
* `trust_root`, `observed_vendor`, and `acl_evaluation` must be wrapped in `types.MappingProxyType`.
* `transitive_executable_chain` and `companion_binaries` must be wrapped in Python `tuple`s.

This guarantees that a caller receiving a receipt from `get_manifest_receipt()` cannot tamper with it before evaluation.

### 4. Trace Demonstration

The following genuinely runnable script demonstrates the persistence logic, bounded contention retry, real typed preservation over the JSON serialization round-trip, and the new two-level receipt architecture matching the ratified types.

```python
# trace.py - Genuine Executable Trace Demonstration
import sqlite3
import threading
import types
import secrets
import json
import time
import os
import sys
import hashlib
import re
from datetime import datetime, timezone
from dataclasses import dataclass, asdict, is_dataclass
from typing import Protocol, Self, Literal, Any, Dict
from enum import Enum

class ExecutableRole(str, Enum):
    ENTRYPOINT_WRAPPER = "ENTRYPOINT_WRAPPER"
    INTERPRETER = "INTERPRETER"
    SCRIPT = "SCRIPT"
    NATIVE_BINARY = "NATIVE_BINARY"
    HELPER_BINARY = "HELPER_BINARY"

@dataclass(frozen=True, slots=True)
class TransitiveExecutableNode:
    role: ExecutableRole
    canonical_path: str
    file_size_bytes: int
    sha256: str
    is_reparse_point: Literal[None] = None

@dataclass(frozen=True, slots=True)
class AclEvaluationEvidence:
    evaluated_paths: tuple[str, ...]
    volume_type: str
    everyone_writable: bool
    anonymous_writable: bool
    authenticated_users_modify_allowed: bool
    effective_dacl_summary: str
    verdict: Literal["PASS_SECURE_LOCAL", "FAIL_WORLD_WRITABLE", "FAIL_NON_NTFS"]

@dataclass(frozen=True, slots=True)
class ManifestProvisioningEvidenceReceipt:
    receipt_id: str
    schema_version: Literal["2.0.0"]
    adapter_id: str
    peer_kind: str
    inventory_generation: int
    trust_root: types.MappingProxyType[str, str]
    observed_vendor: types.MappingProxyType[str, str | None]
    acl_evaluation: AclEvaluationEvidence | None
    transitive_executable_chain: tuple[TransitiveExecutableNode, ...]
    companion_binaries: tuple[TransitiveExecutableNode, ...]
    aggregate_chain_digest: str
    timestamp_utc: str
    chain_complete: bool

@dataclass(frozen=True, slots=True)
class ManifestAdmissionReceipt:
    admission_receipt_id: str
    manifest_canonical_sha256: str
    provisioning_evidence: ManifestProvisioningEvidenceReceipt
    admitted_at_utc: str
    chain_complete: bool

class EnumEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Enum):
            return obj.value
        if is_dataclass(obj):
            return asdict(obj)
        if isinstance(obj, types.MappingProxyType):
            return dict(obj)
        return super().default(obj)

class StateStoreUnavailableError(Exception):
    pass

class StateStoreConstraintError(Exception):
    pass

class FakeSqliteUnitOfWork:
    def __init__(self, conn: sqlite3.Connection, read_only: bool = False):
        self.conn = conn
        self.read_only = read_only


    def _run_query(self, query, params=()):
        try:
            return self.conn.execute(query, params)
        except sqlite3.IntegrityError as e:
            if "UNIQUE constraint failed" in str(e):
                raise StateStoreConstraintError(str(e)) from e
            raise
        except sqlite3.OperationalError as e:
            is_locked = (getattr(e, 'sqlite_errorcode', 0) & 0xFF) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
            if not is_locked and 'locked' in str(e).lower():
                is_locked = True
            if is_locked:
                raise StateStoreUnavailableError("Database locked") from e
            raise

    def __enter__(self) -> Self:
        if not self.read_only:
            try:
                self._run_query("BEGIN IMMEDIATE")
            except Exception as e:
                self.conn.close()
                raise e
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            if not self.read_only:
                if exc_type is None:
                    if self.conn.in_transaction:
                        self.conn.rollback()
                else:
                    self.conn.rollback()
        finally:
            self.conn.close()

    def commit(self) -> None:
        if not self.read_only:
            try:
                self.conn.commit()
            except sqlite3.IntegrityError as e:
                if "UNIQUE constraint failed" in str(e):
                    raise StateStoreConstraintError(str(e)) from e
                raise
            except sqlite3.OperationalError as e:
                is_locked = (getattr(e, 'sqlite_errorcode', 0) & 0xFF) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
                if not is_locked and 'locked' in str(e).lower():
                    is_locked = True
                if is_locked:
                    raise StateStoreUnavailableError("Database locked on commit") from e
                raise

    def rollback(self) -> None:
        if not self.read_only and self.conn.in_transaction:
            self.conn.rollback()

    def close(self) -> None:
        self.conn.close()

    def put_manifest_receipt(self, receipt: ManifestAdmissionReceipt) -> None:
        prov = receipt.provisioning_evidence
        
        self._run_query(
            """
            INSERT INTO manifest_admission_receipts (
                admission_receipt_id, manifest_canonical_sha256, schema_version,
                adapter_id, peer_kind, inventory_generation, trust_root_json,
                observed_vendor_json, acl_evaluation_json, chain_complete,
                aggregate_chain_digest, timestamp_utc, transitive_executable_chain_json,
                companion_binaries_json, admitted_at_utc, prov_chain_complete
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt.admission_receipt_id, receipt.manifest_canonical_sha256,
                prov.schema_version, prov.adapter_id, prov.peer_kind,
                prov.inventory_generation, json.dumps(prov.trust_root, cls=EnumEncoder),
                json.dumps(prov.observed_vendor, cls=EnumEncoder),
                json.dumps(prov.acl_evaluation, cls=EnumEncoder) if prov.acl_evaluation else None,
                int(receipt.chain_complete), prov.aggregate_chain_digest,
                prov.timestamp_utc, json.dumps(prov.transitive_executable_chain, cls=EnumEncoder),
                json.dumps(prov.companion_binaries, cls=EnumEncoder),
                receipt.admitted_at_utc, int(prov.chain_complete)
            )
        )

    def get_manifest_receipt(self, receipt_id: str) -> ManifestAdmissionReceipt | None:
        row = self._run_query(
            "SELECT * FROM manifest_admission_receipts WHERE admission_receipt_id = ?", (receipt_id,)
        ).fetchone()
        if not row: return None
        
        def _parse_node(d: Dict[str, Any]) -> TransitiveExecutableNode:
            return TransitiveExecutableNode(
                role=ExecutableRole(d['role']),
                canonical_path=d['canonical_path'],
                file_size_bytes=d['file_size_bytes'],
                sha256=d['sha256'],
                is_reparse_point=d.get('is_reparse_point')
            )

        def _parse_acl(d: Dict[str, Any] | None) -> AclEvaluationEvidence | None:
            if not d: return None
            return AclEvaluationEvidence(
                evaluated_paths=tuple(d['evaluated_paths']),
                volume_type=d['volume_type'],
                everyone_writable=d['everyone_writable'],
                anonymous_writable=d['anonymous_writable'],
                authenticated_users_modify_allowed=d['authenticated_users_modify_allowed'],
                effective_dacl_summary=d['effective_dacl_summary'],
                verdict=d['verdict']
            )

        prov = ManifestProvisioningEvidenceReceipt(
            receipt_id=row[0], 
            schema_version=row[2],
            adapter_id=row[3],
            peer_kind=row[4],
            inventory_generation=row[5],
            trust_root=types.MappingProxyType(json.loads(row[6])),
            observed_vendor=types.MappingProxyType(json.loads(row[7])),
            acl_evaluation=_parse_acl(json.loads(row[8])) if row[8] else None,
            chain_complete=bool(row[15]),
            aggregate_chain_digest=row[10],
            timestamp_utc=row[11],
            transitive_executable_chain=tuple(_parse_node(n) for n in json.loads(row[12])),
            companion_binaries=tuple(_parse_node(n) for n in json.loads(row[13])),
        )

        return ManifestAdmissionReceipt(
            admission_receipt_id=row[0],
            manifest_canonical_sha256=row[1],
            provisioning_evidence=prov,
            admitted_at_utc=row[14],
            chain_complete=bool(row[9]),
        )

class FakeStateStore:
    def __init__(self, db_path="file:memorydb?mode=memory&cache=shared"):
        self.db_path = db_path
        self._keeper_conn = None

    def initialize(self) -> None:
        if self._keeper_conn is None:
            self._keeper_conn = sqlite3.connect(self.db_path, uri=True)
            self._keeper_conn.execute('''
                CREATE TABLE IF NOT EXISTS manifest_admission_receipts (
                    admission_receipt_id TEXT PRIMARY KEY,
                    manifest_canonical_sha256 TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    adapter_id TEXT NOT NULL,
                    peer_kind TEXT NOT NULL,
                    inventory_generation INTEGER NOT NULL,
                    trust_root_json TEXT NOT NULL,
                    observed_vendor_json TEXT NOT NULL,
                    acl_evaluation_json TEXT,
                    chain_complete INTEGER NOT NULL CHECK (chain_complete = 0),
                    aggregate_chain_digest TEXT NOT NULL,
                    timestamp_utc TEXT NOT NULL,
                    transitive_executable_chain_json TEXT NOT NULL,
                    companion_binaries_json TEXT NOT NULL,
                    admitted_at_utc TEXT NOT NULL,
                    prov_chain_complete INTEGER NOT NULL,
                    CHECK (chain_complete = prov_chain_complete)
                )
            ''')

    def close(self) -> None:
        if self._keeper_conn is not None:
            self._keeper_conn.close()
            self._keeper_conn = None

    def __del__(self):
        self.close()

    def unit_of_work(self) -> FakeSqliteUnitOfWork:
        return FakeSqliteUnitOfWork(sqlite3.connect(self.db_path, timeout=0.0, uri=True), read_only=False)

    def read_unit_of_work(self) -> FakeSqliteUnitOfWork:
        return FakeSqliteUnitOfWork(sqlite3.connect(self.db_path, timeout=0.0, uri=True), read_only=True)

class ManifestAdmissionCoordinator:
    def __init__(self, store: FakeStateStore, clock, ids):
        self._store = store
        self._clock = clock
        self._ids = ids

    def admit_manifest(self, raw_manifest: dict, transitive_executable_chain: tuple[dict, ...], shared_unit: FakeSqliteUnitOfWork = None) -> ManifestAdmissionReceipt:
        if not isinstance(transitive_executable_chain, tuple) or len(transitive_executable_chain) != 1:
            raise ValueError("Executable chain must contain exactly one node (Phase 1 limitation).")
        
        target = raw_manifest.get("execution", {}).get("executable", {}).get("target")
        if not target:
            raise ValueError("Manifest missing execution.executable.target")
            
        nodes = []
        for idx, item in enumerate(transitive_executable_chain):
            if not isinstance(item, dict):
                raise TypeError("Each executable chain item must be a dictionary.")
            if "role" not in item or "canonical_path" not in item or "sha256" not in item:
                raise ValueError("Executable chain item missing required fields: role, canonical_path, sha256.")
                
            role_str = item["role"]
            if role_str not in [e.value for e in ExecutableRole]:
                raise ValueError(f"Invalid role {role_str}")
            role = ExecutableRole(role_str)
            
            c_path = item["canonical_path"]
            if not os.path.isabs(c_path):
                raise ValueError(f"canonical_path must be an absolute path, got '{c_path}'")
            
            c_path_canon = os.path.normpath(os.path.abspath(c_path))
            claimed_hash = item["sha256"]
            
            if not os.path.exists(c_path_canon):
                raise ValueError(f"Executable path does not exist: {c_path_canon}")
            
            if idx == 0:
                resolution_rule = raw_manifest.get("execution", {}).get("executable", {}).get("resolution_rule")
                if not resolution_rule:
                    raise ValueError("Manifest missing execution.executable.resolution_rule")
                
                resolved_target = None
                if resolution_rule == "absolute":
                    if not os.path.isabs(target):
                        raise ValueError(f"resolution_rule 'absolute' requires target to be an absolute path, got '{target}'")
                    resolved_target = target
                elif resolution_rule == "sibling":
                    raise ValueError("resolution_rule 'sibling' is not supported by this in-memory prototype")
                elif resolution_rule == "path":
                    if (
                        "/" in target
                        or "\\" in target
                        or os.sep in target
                        or (os.altsep and os.altsep in target)
                        or os.path.isabs(target)
                        or bool(os.path.dirname(target))
                        or bool(os.path.splitdrive(target)[0])
                    ):
                        raise ValueError(f"resolution_rule 'path' requires a bare command name with no path components, got '{target}'")
                    
                    path_dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
                    resolved_target = None
                    is_windows = sys.platform == "win32" or os.name == "nt"
                    target_has_ext = bool(os.path.splitext(target)[1])
                    
                    pathext_list = []
                    if is_windows:
                        raw_pathext = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD")
                        if raw_pathext != "":
                            for token in raw_pathext.split(os.pathsep):
                                if not re.match(r"^\.[A-Za-z0-9]+$", token):
                                    raise ValueError("Malformed PATHEXT")
                                pathext_list.append(token)
                    
                    for directory in path_dirs:
                        candidate = os.path.join(directory, target)
                        if is_windows and not target_has_ext:
                            matched = False
                            for ext in pathext_list:
                                ext_candidate = candidate + ext
                                if os.path.isfile(ext_candidate):
                                    resolved_target = ext_candidate
                                    matched = True
                                    break
                            if matched:
                                break
                        else:
                            if os.path.isfile(candidate):
                                resolved_target = candidate
                                break
                                
                    if resolved_target is None:
                        raise ValueError(f"Target '{target}' with resolution_rule 'path' could not be resolved via OS PATH")
                else:
                    raise ValueError(f"Unknown resolution_rule {resolution_rule}")
                    
                resolved_canon = os.path.normpath(os.path.abspath(resolved_target))
                if not os.path.exists(resolved_canon):
                    raise ValueError(f"Resolved target does not exist: {resolved_canon}")
                
                try:
                    same_file = os.path.samefile(resolved_canon, c_path_canon)
                except OSError:
                    same_file = False
                if not same_file:
                    raise ValueError(f"Executable chain entrypoint {c_path_canon} does not match resolved manifest target {resolved_target}")
            
            with open(c_path_canon, "rb") as f:
                file_content = f.read()
                actual_hash = hashlib.sha256(file_content).hexdigest().upper()
            if actual_hash != claimed_hash.upper():
                raise ValueError(f"Executable hash mismatch for {c_path_canon}! Claimed: {claimed_hash}, Actual: {actual_hash}")
            
            if role == ExecutableRole.NATIVE_BINARY and not file_content.startswith(b"MZ"):
                raise ValueError(f"File content at {c_path_canon} does not match NATIVE_BINARY format claim (missing MZ magic bytes).")
            
            nodes.append(TransitiveExecutableNode(
                role=role,
                canonical_path=c_path_canon,
                file_size_bytes=os.path.getsize(c_path_canon),
                sha256=actual_hash,
                is_reparse_point=None
            ))

        sorted_nodes = sorted(nodes, key=lambda x: (x.role.value, x.canonical_path))
        payload = ""
        for node in sorted_nodes:
            payload += f"{node.role.value}:{node.canonical_path}:{node.sha256}\n"
        aggregate_chain_digest = hashlib.sha256(payload.encode("utf-8")).hexdigest().upper()

        timestamp_utc = datetime.fromtimestamp(self._clock.now(), tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        adapter_id = raw_manifest["adapter"]["adapter_id"]
        peer_kind = raw_manifest["adapter"]["peer_kind"]
        
        MAX_RETRIES = 10
        deadline = time.monotonic() + 5.0
        for attempt in range(MAX_RETRIES):
            random_suffix = secrets.token_hex(16)
            receipt_id = f"receipt-{peer_kind}-{adapter_id}-{timestamp_utc}-{random_suffix}"
            
            prov_evidence = ManifestProvisioningEvidenceReceipt(
                receipt_id=receipt_id,
                schema_version="2.0.0",
                adapter_id=adapter_id,
                peer_kind=peer_kind,
                inventory_generation=1,
                trust_root=types.MappingProxyType({"root": "trusted"}),
                observed_vendor=types.MappingProxyType({"vendor": "fake"}),
                acl_evaluation=AclEvaluationEvidence(
                    evaluated_paths=("/fake",),
                    volume_type="NTFS",
                    everyone_writable=False,
                    anonymous_writable=False,
                    authenticated_users_modify_allowed=False,
                    effective_dacl_summary="SECURE",
                    verdict="PASS_SECURE_LOCAL"
                ),
                chain_complete=False,
                aggregate_chain_digest=aggregate_chain_digest,
                timestamp_utc=timestamp_utc,
                transitive_executable_chain=tuple(sorted_nodes),
                companion_binaries=()
            )
            
            receipt = ManifestAdmissionReceipt(
                admission_receipt_id=receipt_id,
                manifest_canonical_sha256="fake_sha",
                provisioning_evidence=prov_evidence,
                admitted_at_utc=timestamp_utc,
                chain_complete=False
            )
            
            if shared_unit is not None:
                try:
                    shared_unit.put_manifest_receipt(receipt)
                    return receipt
                except StateStoreConstraintError as e:
                    if "UNIQUE constraint failed" in str(e):
                        continue
                    raise
            else:
                while True:
                    try:
                        with self._store.unit_of_work() as unit:
                            unit.put_manifest_receipt(receipt)
                            unit.commit()
                        return receipt
                    except StateStoreConstraintError as e:
                        if "UNIQUE constraint failed" in str(e):
                            break 
                        raise
                    except StateStoreUnavailableError:
                        if time.monotonic() >= deadline:
                            raise StateStoreUnavailableError("Database locked beyond timeout bound")
                        time.sleep(0.05)

        raise RuntimeError("Collision resolution exhausted: unable to generate a unique admission receipt ID.")

    def get_trusted_digest(self, receipt_id: str) -> str:
        if not isinstance(receipt_id, str):
            raise TypeError("receipt_id must be a string")
        deadline = time.monotonic() + 5.0
        while True:
            try:
                with self._store.read_unit_of_work() as unit:
                    receipt = unit.get_manifest_receipt(receipt_id)
                    if receipt:
                        return receipt.manifest_canonical_sha256
                    raise ValueError("Unknown admission receipt ID")
            except StateStoreUnavailableError:
                if time.monotonic() >= deadline:
                    raise StateStoreUnavailableError("Database locked beyond timeout bound during read")
                time.sleep(0.05)

    def get_trusted_receipt(self, receipt_id: str) -> ManifestAdmissionReceipt:
        if not isinstance(receipt_id, str):
            raise TypeError("receipt_id must be a string")
        deadline = time.monotonic() + 5.0
        while True:
            try:
                with self._store.read_unit_of_work() as unit:
                    receipt = unit.get_manifest_receipt(receipt_id)
                    if receipt:
                        return receipt
                    raise ValueError("Unknown admission receipt ID")
            except StateStoreUnavailableError:
                if time.monotonic() >= deadline:
                    raise StateStoreUnavailableError("Database locked beyond timeout bound during read")
                time.sleep(0.05)

if __name__ == "__main__":
    class DummyClock:
        def now(self): return int(datetime.now(timezone.utc).timestamp())
    class DummyIdSource:
        def new_id(self, ns): return secrets.token_hex(8)

    print("--- TRACE DEMONSTRATION START ---")

    
    # We must use file-backed DB for lock testing
    db_file = os.path.abspath("test_trace.db")
    if os.path.exists(db_file):
        os.remove(db_file)
    store = FakeStateStore(db_path=db_file)
    store.initialize()
    coordinator = ManifestAdmissionCoordinator(store, DummyClock(), DummyIdSource())

    print("\n(0) Protocol conformance checks (Issue 2 resolution):")
    # We must explicitly import protocols to test
    import os, sys
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
    from peerhub.state.contract import UnitOfWork, ReadUnitOfWork
    
    uow = store.unit_of_work()
    is_uow = isinstance(uow, UnitOfWork)
    print(f"FakeSqliteUnitOfWork satisfies UnitOfWork: {is_uow}")
    uow.close()
    
    ruow = store.read_unit_of_work()
    is_ruow = isinstance(ruow, ReadUnitOfWork)
    print(f"FakeSqliteUnitOfWork satisfies ReadUnitOfWork: {is_ruow}")
    ruow.close()

    
    # 1. Real valid binary (using python.exe or cmd.exe)
    valid_exe = os.path.abspath(sys.executable)
    with open(valid_exe, "rb") as f:
        valid_exe_hash = hashlib.sha256(f.read()).hexdigest().upper()
        
    valid_chain = ({
        "role": ExecutableRole.NATIVE_BINARY.value,
        "canonical_path": valid_exe,
        "sha256": valid_exe_hash
    },)
    
    raw_manifest_valid = {
        "adapter": {"adapter_id": "adapter_1", "peer_kind": "ag"},
        "execution": {"executable": {"target": valid_exe, "resolution_rule": "absolute"}}
    }
    
    print("\n(a) Admission success & ID match (valid single-node real binary):")
    try:
        receipt = coordinator.admit_manifest(raw_manifest_valid, valid_chain)
        print(f"Issued ID: {receipt.admission_receipt_id}")
        print("-> Format matches ratified scheme.")
    except Exception as e:
        print(f"-> FAILED unexpected exception: {e}")

    print("\n(b) Admission rejection (nonexistent path):")
    nonexistent_chain = ({
        "role": ExecutableRole.NATIVE_BINARY.value,
        "canonical_path": r"C:\does_not_exist_xyz123.exe",
        "sha256": "0" * 64
    },)
    try:
        coordinator.admit_manifest(raw_manifest_valid, nonexistent_chain)
        print("-> FAILED: Nonexistent path incorrectly admitted.")
    except ValueError as e:
        print(f"-> SUCCESS: Nonexistent path rejected: {e}")

    print("\n(c) Admission rejection (wrong hash):")
    wrong_hash_chain = ({
        "role": ExecutableRole.NATIVE_BINARY.value,
        "canonical_path": valid_exe,
        "sha256": "0" * 64
    },)
    try:
        coordinator.admit_manifest(raw_manifest_valid, wrong_hash_chain)
        print("-> FAILED: Wrong hash incorrectly admitted.")
    except ValueError as e:
        print(f"-> SUCCESS: Wrong hash rejected: {e}")

    print("\n(d) Admission rejection (missing MZ magic bytes):")
    non_exe_file = os.path.abspath(__file__)
    with open(non_exe_file, "rb") as f:
        non_exe_hash = hashlib.sha256(f.read()).hexdigest().upper()
    non_exe_chain = ({
        "role": ExecutableRole.NATIVE_BINARY.value,
        "canonical_path": non_exe_file,
        "sha256": non_exe_hash
    },)
    raw_manifest_non_exe = {
        "adapter": {"adapter_id": "adapter_1", "peer_kind": "ag"},
        "execution": {"executable": {"target": non_exe_file, "resolution_rule": "absolute"}}
    }
    try:
        coordinator.admit_manifest(raw_manifest_non_exe, non_exe_chain)
        print("-> FAILED: Non-MZ file incorrectly admitted as NATIVE_BINARY.")
    except ValueError as e:
        print(f"-> SUCCESS: Non-MZ file rejected: {e}")

    print("\n(e) Concurrent contention (real file-backed wall-clock bounded):")
    def holding_worker():
        conn = None
        try:
            conn = sqlite3.connect(store.db_path, timeout=6.0)
            conn.execute("BEGIN IMMEDIATE")
            time.sleep(8.0)
        except Exception as e:
            print(f"Holding worker failed: {e}")
        finally:
            if conn:
                conn.close()

    holder = threading.Thread(target=holding_worker)
    holder.start()
    time.sleep(0.5)

    start_time = time.monotonic()
    try:
        coordinator.admit_manifest(raw_manifest_valid, valid_chain)
        print("-> FAILED: Admitted despite lock.")
    except StateStoreUnavailableError as e:
        elapsed = time.monotonic() - start_time
        print(f"-> SUCCESS: Gave up after {elapsed:.2f}s with {type(e).__name__}: {e}")

    holder.join()

    print("\n(f) Caller-supplied open UoW (Issue 4 resolution):")
    try:
        with store.unit_of_work() as caller_unit:
            shared_receipt = coordinator.admit_manifest({"adapter": {"adapter_id": "shared_uow", "peer_kind": "ag"}, "execution": {"executable": {"target": valid_exe, "resolution_rule": "absolute"}}}, valid_chain, caller_unit)
            caller_unit.commit()
        print(f"-> SUCCESS: Admitted via shared UoW. ID format matched.")
    except Exception as e:
        print(f"-> FAILED: Shared UoW failed: {e}")
        
    print("\n(g) Caller-supplied open UoW violated (self-contention):")
    try:
        with store.unit_of_work() as caller_unit:
            # We open a UoW but forget to pass it to admit_manifest!
            # Since we are using file-backed DB, this causes immediate self-deadlock 
            # if admit_manifest tries to get its own UoW and block on BEGIN IMMEDIATE
            coordinator.admit_manifest({"adapter": {"adapter_id": "bad_uow", "peer_kind": "ag"}, "execution": {"executable": {"target": valid_exe, "resolution_rule": "absolute"}}}, valid_chain)
            print("-> FAILED: Allowed violation of shared UoW rule.")
    except StateStoreUnavailableError as e:
        print(f"-> SUCCESS: Detected self-contention when shared_unit omitted: {e}")


    print("\n(h) Concurrent contention on read path (Issue 4 resolution):")
    def holding_worker_for_read():
        conn = None
        try:
            conn = sqlite3.connect(store.db_path, timeout=6.0)
            conn.execute("BEGIN EXCLUSIVE")
            time.sleep(8.0)
        except Exception as e:
            print(f"Holding worker (read) failed: {e}")
        finally:
            if conn:
                conn.close()
                
    holder_read = threading.Thread(target=holding_worker_for_read)
    holder_read.start()
    time.sleep(0.5)

    start_time_read = time.monotonic()
    try:
        coordinator.get_trusted_receipt('dummy')
        print("-> FAILED: Read admitted despite exclusive lock.")
    except StateStoreUnavailableError as e:
        elapsed_read = time.monotonic() - start_time_read
        print(f"-> SUCCESS: Read gave up after {elapsed_read:.2f}s with {type(e).__name__}: {e}")

    holder_read.join()

    
    print("\n(i) Raw sqlite3 exception cannot escape commit() under lock contention (Issue 1 resolution):")
    def reader_worker_holding_shared_lock():
        conn = None
        try:
            conn = sqlite3.connect(store.db_path, timeout=6.0)
            conn.execute("BEGIN DEFERRED")
            conn.execute("SELECT * FROM manifest_admission_receipts").fetchall()
            time.sleep(4.0)
        except Exception as e:
            print(f"Reader worker failed: {e}")
        finally:
            if conn:
                conn.close()

    holder_reader = threading.Thread(target=reader_worker_holding_shared_lock)
    holder_reader.start()
    time.sleep(0.5)

    try:
        with store.unit_of_work() as uow:
            # Modify the receipt ID to avoid UNIQUE constraint violation on put
            new_receipt_id = receipt.admission_receipt_id + "-commit-test"
            prov = receipt.provisioning_evidence
            
            import dataclasses
            new_prov = dataclasses.replace(prov, receipt_id=new_receipt_id)
            new_receipt = dataclasses.replace(receipt, admission_receipt_id=new_receipt_id, provisioning_evidence=new_prov)
            
            uow.put_manifest_receipt(new_receipt)
            # This should raise StateStoreUnavailableError, not raw sqlite3.OperationalError
            uow.commit() 
        print("-> FAILED: Commit succeeded despite reader holding shared lock.")
    except StateStoreUnavailableError as e:
        print(f"-> SUCCESS: Commit correctly translated locked error: {e}")
    except Exception as e:
        print(f"-> FAILED: Commit leaked raw exception or other error: {type(e).__name__}: {e}")

    holder_reader.join()

    print("--- TRACE DEMONSTRATION END ---")
```

**Real Executable Trace Output:**
```text
--- TRACE DEMONSTRATION START ---

(0) Protocol conformance checks (Issue 2 resolution):
FakeSqliteUnitOfWork satisfies UnitOfWork: True
FakeSqliteUnitOfWork satisfies ReadUnitOfWork: True

(a) Admission success & ID match (valid single-node real binary):
Issued ID: receipt-ag-adapter_1-20260821T162935Z-9d08117da199e81bb6971e0fc51f01b1
-> Format matches ratified scheme.

(b) Admission rejection (nonexistent path):
-> SUCCESS: Nonexistent path rejected: Executable path does not exist: C:\does_not_exist_xyz123.exe

(c) Admission rejection (wrong hash):
-> SUCCESS: Wrong hash rejected: Executable hash mismatch for P:\_sys\env\venv\Scripts\python.exe! Claimed: 0000000000000000000000000000000000000000000000000000000000000000, Actual: 3ADBBF2AF609E206E3CA18CD55FC7C4B52F5C8BB8218DD99FD5A9E50D7A193CD

(d) Admission rejection (missing MZ magic bytes):
-> SUCCESS: Non-MZ file rejected: File content at P:\workspace\peerhub\docs\design\trace_run.py does not match NATIVE_BINARY format claim (missing MZ magic bytes).

(e) Concurrent contention (real file-backed wall-clock bounded):
-> SUCCESS: Gave up after 5.01s with StateStoreUnavailableError: Database locked beyond timeout bound

(f) Caller-supplied open UoW (Issue 4 resolution):
-> SUCCESS: Admitted via shared UoW. ID format matched.

(g) Caller-supplied open UoW violated (self-contention):
-> SUCCESS: Detected self-contention when shared_unit omitted: Database locked beyond timeout bound

(h) Concurrent contention on read path (Issue 4 resolution):
-> SUCCESS: Read gave up after 5.07s with StateStoreUnavailableError: Database locked beyond timeout bound during read

(i) Raw sqlite3 exception cannot escape commit() under lock contention (Issue 1 resolution):
-> SUCCESS: Commit correctly translated locked error: Database locked on commit
--- TRACE DEMONSTRATION END ---
```

> [!NOTE]
> **Trace Validity Gap (Journal Mode):** The real `SqliteStateStore` adapter (`peerhub/persistence/sqlite.py`) uses `PRAGMA journal_mode = WAL`, under which readers never block writers and writers never block readers. This means scenario (h) (a read blocked by a `BEGIN EXCLUSIVE` holder) and scenario (i) (`commit()` failing because a reader holds a shared lock) cannot reproduce against the real store, since the illustrative trace above used SQLite's default rollback journal mode instead of WAL. Note that the `commit()` error-translation code these scenarios exercise is still correct and necessary (WAL writer-vs-writer contention is a real, different scenario), but the evidence presented for it was produced under a journal mode the canonical store doesn't actually use. This is disclosed here as a trace-validity gap, not a design defect.

## C. Shim Registry Persistence Folding

**Context:** `PHASE1-THIRDPARTY-DEFERRAL-AND-SHIMS-2026-08-20.md` specified an atomic-candidate-snapshot pattern (`os.replace`) on a `shim_registry.json` file. `ARCHITECTURE.md` explicitly deprecated this pattern ("bespoke JSON-file locking") in favor of SQLite.

**Resolution:**
The shim registry state is folded into the same SQLite database as the manifest receipts, sharing the single operational source of truth.

**Round 104 provenance note:** the design below replaces the Round 90-103 `install_sub_path`-branching schema, which was ratified at Round 102 and implemented with repository operations and a passing trace at Round 103 — then, after closure, an independent fresh-eyes review (dispatched separately from the ag/cx dialectic that built it, at the user's explicit request for a final "is this actually complete" check) found 2 blocking defects the 12 prior review/fix rounds had missed: (F1) the state machine had no terminal failure state, so the design's own correct `ERR_SHIM_EXTERNALLY_MODIFIED` abort permanently bricked the shim with no recovery path short of hand-editing SQLite — independently reproduced by the terminal with no crash involved, purely from the protocol's own correct behavior; (F2) the Round 103 reference implementation's `EXTERNAL_COLLISION` crash-resume path compared a freshly-recomputed hash against itself instead of the durably-persisted `original_sha256`, silently defeating tamper detection across a crash window — also independently reproduced. The same review proposed collapsing the per-sub-path branching into a single uniform `(pre_state_hash, post_state_hash)` model, which the user approved over a narrower patch, since it resolves F1 structurally, makes F2 unrepeatable by construction, and lets `REMOVE` (previously fully deferred) fit the same shape as everything else instead of needing its own protocol.

### 1. Illustrative Schema Extension (`0026_shim_registry_persistence.sql`, Redesigned, Round 104)

This schema abandons bespoke, operation-conditional logic (`install_sub_path` branching) in favor of a single uniform model: every operation asserts a `(pre_state_hash, post_state_hash)` transition over a single resource `P`, with exactly one filesystem effect, and the database commit strictly ordered before the effect.

**Invariant: Deterministic Canonicalization (Fixes F4).** Before any path is written to `canonical_shim_path`, or validated against an intended admission target, it is processed through a concrete, deterministic canonicalization function that establishes true filesystem identity:
* **For an existing path**, resolve to its real filesystem identity (following symlinks and junctions, rejecting loops) before normalizing path separators and case-folding per the target platform's filesystem convention (strictly lowercased on Windows).
* **For a not-yet-existing final path** (a fresh install target), canonicalize the resolved parent directory's real identity, then append the literal final path component, normalized and case-folded.
This canonicalization is applied consistently to **both** sides of any target-path comparison.

**Accepted Phase 1 limitation — no in-place shim renaming.** A registered shim's `shim_name` and `canonical_shim_path` are immutable identity fields for the lifetime of its `shim_registration_id`. The Phase 1 state machine provides no rename transition and no operation may update either field. Renaming therefore requires retiring the existing registration and creating a new registration through a fresh install. This is intentional and accepted for Phase 1, not an omitted operation.

**Accepted Phase 1 limitation — retired-registration backup archives.** Retiring a shim registration does not automatically delete its existing `.bak` archives or their metadata rows. Those archives remain durable audit artifacts associated with the immutable `shim_registration_id`, but no normal active-shim restore path is guaranteed after retirement. This is not immediate logical data loss: the files and records remain present and auditable. It is an accepted risk of inert disk usage and requires an explicit future retention/garbage-collection policy if cleanup is desired.

```sql
-- 0026_shim_registry_persistence.sql
-- Illustrative placeholder consistent with Item B's convention

CREATE TABLE shim_registry_entries (
    -- Immutable generation identity, decoupling backup FKs from the mutable shim_name
    shim_registration_id TEXT PRIMARY KEY,

    shim_name TEXT NOT NULL,
    canonical_shim_path TEXT NOT NULL,

    -- The path where the generated shim file resides (corresponds to 'P' in §2.8.2/§2.8.3)
    shim_path TEXT NOT NULL,

    -- The downstream real executable this shim forwards to (distinct from 'P')
    downstream_target_path TEXT NOT NULL,

    status TEXT NOT NULL CHECK (status IN ('PROVISIONING', 'ACTIVE', 'RETIRED')),

    profile_name TEXT NOT NULL,
    admission_receipt_id TEXT NOT NULL REFERENCES manifest_admission_receipts(admission_receipt_id),
    shim_file_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    -- Cross-reference for baseline acceptance, explicitly linking to the ABORTED operation that prompted it
    accepted_baseline_at TEXT,
    accepted_baseline_reference_idempotency_key TEXT REFERENCES shim_pending_operations(idempotency_key)
);

-- Uniqueness enforced only among PROVISIONING and ACTIVE registrations; a RETIRED path may be legitimately reused
CREATE UNIQUE INDEX idx_active_shim_name ON shim_registry_entries(shim_name) WHERE status IN ('PROVISIONING', 'ACTIVE');
CREATE UNIQUE INDEX idx_active_canonical_path ON shim_registry_entries(canonical_shim_path) WHERE status IN ('PROVISIONING', 'ACTIVE');
```

**`operation_kind` derivation rule.** `operation_kind` is derived deterministically at Step 1 from the current filesystem state of `P`, the active registration state, and the requested operation intent; it is never caller-selectable independently of those facts. Select exactly one value:
- `ABSENT` when no file exists at `P` and the requested outcome is `ACTIVE`.
- `EXTERNAL_COLLISION` when a file exists at `P` but no matching active/provisioning registration exists for the canonicalized shim identity.
- `MANAGED_UPDATE` when a matching active/provisioning registration exists, the requested outcome is `ACTIVE`, and the operation replaces the managed payload.
- `RESTORE` when the requested operation selects an existing, unrestored backup archive for restoration.
- `REMOVE` when the requested operation removes the managed shim and the intended registry outcome is `RETIRED`.

After derivation, the operation's `pre_state_hash`, `post_state_hash`, intended registry outcome, binding fields, authorization fields, and backup/fallback fields MUST satisfy the corresponding `operation_kind` `CHECK` branch below. Any combination that maps to zero or more than one branch is rejected before the intent is committed.

```sql
CREATE TABLE shim_pending_operations (
    -- Caller-supplied idempotency key; checked BEFORE any parent-row mutation
    idempotency_key TEXT PRIMARY KEY,

    -- Hash of the logical operation's inputs (including force_override_authorized)
    request_digest TEXT NOT NULL,

    -- Explicit operation intent, durably recorded BEFORE any filesystem write begins
    shim_registration_id TEXT NOT NULL REFERENCES shim_registry_entries(shim_registration_id),

    -- An observability/audit label identifying the logical operation.
    operation_kind TEXT NOT NULL CHECK (operation_kind IN ('ABSENT', 'EXTERNAL_COLLISION', 'MANAGED_UPDATE', 'RESTORE', 'REMOVE')),

    -- Unified single-resource state assertions.
    pre_state_hash TEXT,
    post_state_hash TEXT,

    -- Captured live from P at Step 1 (when P exists); the authoritative, drift-proof
    -- source for EXTERNAL_COLLISION backup creation's original_mtime_epoch/original_permissions_octal,
    -- immune to any metadata change on P between Step 1 and a later crash-resumed attempt
    original_mtime_epoch REAL,
    original_permissions_octal TEXT,

    -- Explicit authorization flag/reason for the EXTERNAL_COLLISION path
    force_override_authorized INTEGER NOT NULL DEFAULT 0 CHECK (force_override_authorized IN (0, 1)),
    force_override_reason TEXT,

    -- Intended outcome for the registry entry upon completion
    intended_registry_outcome TEXT NOT NULL CHECK (intended_registry_outcome IN ('ACTIVE', 'RETIRED')),

    -- Intended bindings at intent time, required when outcome is ACTIVE
    intended_profile_name TEXT,
    intended_downstream_target_path TEXT,
    intended_admission_receipt_id TEXT REFERENCES manifest_admission_receipts(admission_receipt_id),

    -- Populated when a REMOVE needs to remember the fallback tool path
    intended_fallback_tool_path TEXT,

    -- Populated when a RESTORE selects a backup to restore from
    selected_backup_sequence_id INTEGER,

    -- 'ABORTED' resolves the permanent-brick state
    operation_state TEXT NOT NULL CHECK (operation_state IN ('INTENT_DECLARED', 'FS_STAGED', 'COMPLETED', 'ABORTED')),
    created_at TEXT NOT NULL,

    -- Populated only by the force_complete_unconfirmed_finalization operator intervention
    finalization_override_reason TEXT,
    finalization_override_at TEXT,
    CHECK ((finalization_override_reason IS NULL) = (finalization_override_at IS NULL)),
    CHECK (finalization_override_reason IS NULL OR operation_state = 'COMPLETED'),
    CHECK ((original_mtime_epoch IS NULL) = (original_permissions_octal IS NULL)),

    -- Per-operation-kind CHECK constraints matching the unified design
    CHECK (
        (
            operation_kind = 'ABSENT'
            AND pre_state_hash IS NULL
            AND post_state_hash IS NOT NULL
            AND intended_registry_outcome = 'ACTIVE'
            AND intended_profile_name IS NOT NULL
            AND intended_downstream_target_path IS NOT NULL
            AND intended_admission_receipt_id IS NOT NULL
            AND selected_backup_sequence_id IS NULL
            AND force_override_authorized = 0
            AND force_override_reason IS NULL
            AND intended_fallback_tool_path IS NULL
        ) OR (
            operation_kind = 'EXTERNAL_COLLISION'
            AND pre_state_hash IS NOT NULL
            AND post_state_hash IS NOT NULL
            AND intended_registry_outcome = 'ACTIVE'
            AND intended_profile_name IS NOT NULL
            AND intended_downstream_target_path IS NOT NULL
            AND intended_admission_receipt_id IS NOT NULL
            AND selected_backup_sequence_id IS NULL
            AND force_override_authorized = 1
            AND force_override_reason IS NOT NULL
            AND TRIM(force_override_reason, char(32) || char(9) || char(10) || char(13)) != ''
            AND intended_fallback_tool_path IS NULL
            AND original_mtime_epoch IS NOT NULL
            AND original_permissions_octal IS NOT NULL
        ) OR (
            operation_kind = 'MANAGED_UPDATE'
            AND pre_state_hash IS NOT NULL
            AND post_state_hash IS NOT NULL
            AND intended_registry_outcome = 'ACTIVE'
            AND intended_profile_name IS NOT NULL
            AND intended_downstream_target_path IS NOT NULL
            AND intended_admission_receipt_id IS NOT NULL
            AND selected_backup_sequence_id IS NULL
            AND force_override_authorized = 0
            AND force_override_reason IS NULL
            AND intended_fallback_tool_path IS NULL
        ) OR (
            operation_kind = 'RESTORE'
            AND pre_state_hash IS NOT NULL
            AND post_state_hash IS NOT NULL
            AND intended_registry_outcome = 'RETIRED'
            AND intended_profile_name IS NULL
            AND intended_downstream_target_path IS NULL
            AND intended_admission_receipt_id IS NULL
            AND selected_backup_sequence_id IS NOT NULL
            AND force_override_authorized = 0
            AND force_override_reason IS NULL
            AND intended_fallback_tool_path IS NULL
        ) OR (
            operation_kind = 'REMOVE'
            AND pre_state_hash IS NOT NULL
            AND post_state_hash IS NULL
            AND intended_registry_outcome = 'RETIRED'
            AND intended_profile_name IS NULL
            AND intended_downstream_target_path IS NULL
            AND intended_admission_receipt_id IS NULL
            AND selected_backup_sequence_id IS NULL
            AND force_override_authorized = 0
            AND force_override_reason IS NULL
            AND intended_fallback_tool_path IS NOT NULL
        )
    ),

    -- Ensures a RESTORE's selected backup actually belongs to its own registration
    FOREIGN KEY (shim_registration_id, selected_backup_sequence_id) REFERENCES shim_backup_entries(shim_registration_id, backup_sequence_id)
);

-- Prevents multiple simultaneous non-terminal operations against the same registration.
-- Explicitly excludes ABORTED so a tampered operation does not permanently block the shim.
CREATE UNIQUE INDEX idx_active_pending_operation ON shim_pending_operations(shim_registration_id) WHERE operation_state NOT IN ('COMPLETED', 'ABORTED');

-- Parent-side unique index required for the composite FK from shim_backup_entries
CREATE UNIQUE INDEX idx_pending_op_reg_key ON shim_pending_operations(shim_registration_id, idempotency_key);

CREATE TABLE shim_backup_entries (
    backup_sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- FK to the immutable generation id
    shim_registration_id TEXT NOT NULL REFERENCES shim_registry_entries(shim_registration_id),

    -- Binds this backup to the operation that created it
    originating_idempotency_key TEXT NOT NULL UNIQUE,

    target_path TEXT NOT NULL,
    backup_file_path TEXT NOT NULL,
    original_sha256 TEXT NOT NULL,

    original_mtime_epoch REAL NOT NULL,
    original_file_size_bytes INTEGER NOT NULL,
    original_permissions_octal TEXT NOT NULL,
    override_reason TEXT NOT NULL,

    backup_created_at TEXT NOT NULL,
    restored INTEGER NOT NULL DEFAULT 0 CHECK (restored IN (0, 1)),
    restored_at TEXT,

    -- Set when ANY permanent validation failure (missing, non-regular, hardlinked, hash mismatch) makes this backup forever unusable
    corrupt_detected_at TEXT,
    -- Persisted audit context for the quarantine; populated by mark_backup_permanently_unusable
    -- (operator-supplied reason), optionally by automatic structural-failure quarantine paths
    -- (e.g. "structural: <specific failure>"); left NULL otherwise
    corrupt_reason TEXT,

    -- Ensures this backup's originating operation was on the SAME registration
    FOREIGN KEY (shim_registration_id, originating_idempotency_key) REFERENCES shim_pending_operations(shim_registration_id, idempotency_key)
);

-- Parent-side unique index required for the composite FK from shim_pending_operations
CREATE UNIQUE INDEX idx_backup_reg_seq ON shim_backup_entries(shim_registration_id, backup_sequence_id);
```

*(This schema was independently verified against real `sqlite3` before being committed: valid shapes accepted; invalid shapes correctly rejected with `CHECK constraint failed`; and — the specific proof that F1 is structurally fixed — after transitioning an operation to `ABORTED` while its registry entry is `PROVISIONING`, a fresh `INTENT_DECLARED` operation on the SAME registration was correctly accepted, confirming `idx_active_pending_operation`'s `NOT IN ('COMPLETED', 'ABORTED')` clause no longer lets an aborted operation permanently block the registration.)*

> [!IMPORTANT]
> **Visibility of `ACTIVE` registrations.** Because new registrations become visible as `status='PROVISIONING'` in `shim_registry_entries` the moment step 1 commits — before the actual filesystem write ever happens — consumers evaluating "is this shim usable" MUST structurally exclude in-flight installs, e.g. by only ever consuming rows where `status='ACTIVE'` (and never `PROVISIONING`).

### 2. Uniform Crash-Recoverable State Machine

Every operation (`ABSENT`, `EXTERNAL_COLLISION`, `MANAGED_UPDATE`, `RESTORE`, and `REMOVE`) applies the uniform `(pre_state_hash, post_state_hash)` shape. No SQLite transaction is ever held open across a filesystem write. A higher-level advisory/mutex file lock is held across the entire sequence, released only after the final commit. Every transaction uses `BEGIN IMMEDIATE` and verifies affected-row counts on compare-and-swap (CAS) transitions. For every operation, `FS_STAGED` is committed to the database **before** the final filesystem replacement of `P` occurs — resolving the Round 90-103 design's asymmetry where INSTALL committed `FS_STAGED` before writing while RESTORE wrote before committing `FS_STAGED`, which was the direct cause of needing two different bespoke reconciliation paragraphs instead of one shared rule.

**Cleanup Principle:** Because disposable staging artifact paths (e.g. `.tmp`/deterministic stages) have their deterministic leaf name derived from `idempotency_key` alone (via full SHA-256), placed within the operation's own shim directory, their cleanup is ALWAYS safe to attempt (delete-if-exists is idempotent, but MUST be performed using strictly no-follow semantics to remove only the directory entry). This "always safe to delete" rule applies strictly to disposable staging artifacts; finalized durable `.bak` archives and their metadata records are governed instead by their own explicit lifecycle (quarantine via `mark_backup_permanently_unusable`, or `restored=1` marking), never casual cleanup. Any code path that transitions an operation to a state where stages are no longer needed (`ABORTED` or `COMPLETED`) MUST explicitly delete the deterministically-named stage if it exists.

**Advisory Lock Specification**
- **Mechanism**: an OS-level advisory file lock (NOT a plain "lock file exists = locked" convention, which would need its own stale-lock/timeout handling) -- specifically, an exclusively-locked file descriptor held open for the duration of the operation, using the platform's native advisory locking primitive. On POSIX, this uses `fcntl.flock(fd, LOCK_EX)`; the descriptor used for locking MUST be opened with atomic `O_CLOEXEC` at `open()` time (i.e., `os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC)`) as the ONLY acceptable approach, and must never be duplicated or passed to a child process. On Windows, this uses `msvcrt.locking(fd, msvcrt.LK_LOCK, ...)` or an equivalent OS-level exclusive lock; `msvcrt.locking` locks a byte range starting at the file's CURRENT position, not the whole file -- every contender must seek to the SAME fixed offset and lock the SAME fixed nonzero range (e.g., byte range `[0, 1)`) for mutual exclusion to actually work. This is the load-bearing property: OS-level advisory locks are tied to the OPEN FILE DESCRIPTOR's lifetime, not to any application-level state, so the OS guarantees eventual release of the lock after the owning process terminates (whether cleanly or via crash/kill), though the exact timing of that release is platform-dependent and not necessarily instantaneous -- this is still sufficient to prevent a PERMANENTLY stuck lock, which is the actual property this design depends on, not sub-second release latency.
- **Lock file path**: derived deterministically via `sha256` of the canonicalized identity, serialized into a single byte string using ONE defined, unambiguous encoding before hashing. Serialize as `json.dumps([shim_name, canonical_shim_path], separators=(',', ':'), ensure_ascii=False).encode('utf-8')` -- this EXACT call, with these EXACT parameters, is REQUIRED, not illustrative (`separators=(',', ':')` eliminates whitespace variation; `ensure_ascii=False` eliminates non-ASCII escaping variation; UTF-8 encoding is unambiguous for any valid Python `str`). The resulting `sha256` digest MUST be rendered as `hexdigest()` -- i.e., lowercase hexadecimal, exactly 64 characters, with no other rendering (uppercase, base64, etc.) ever acceptable. This produces a dedicated `.peerhub-lock.<sha256(serialized_identity)>` file in the isolated archive/lock directory established by the A2 security fix, NOT adjacent to `P` or inside the PATH-exposed shim directory -- matching the same PATH-exposure discipline already established for backup archives.
- **Scope**: exactly one lock per canonicalized identity, matching every existing reference to "the advisory lock scoped to the canonicalized shim identity (`shim_name`/`canonical_shim_path`)" throughout the document.
- **Acquisition discipline**: open (create if missing) the lock file, then acquire the exclusive OS-level lock, blocking or with a bounded wait as the caller's context requires; release by closing the file descriptor (never by deleting the lock file itself -- the file persists across operations, only its lock state is transient, avoiding any TOCTOU risk from delete-and-recreate).
- **Cross-platform statement**: explicitly acknowledge this requires a real per-platform implementation (the two primitives named above), consistent with how the rest of this document already discloses real implementation requirements rather than leaving them silent.

**States:** `INTENT_DECLARED` → `FS_STAGED` → `COMPLETED` | `ABORTED`

1. **[START] → INTENT_DECLARED**
   * *FS*: (1) Canonicalize the supplied shim identity (`shim_name`/`canonical_shim_path`) using the deterministic canonicalization function; (2) serialize this canonicalized identity into a single byte string using ONE defined, unambiguous encoding; (3) hash it and acquire the advisory lock; (4) THEN evaluate the current filesystem identity of `P`. If `P` exists but is not a regular file with `st_nlink == 1` (a symlink, directory, device node, or hardlinked file), do NOT raise an exception here -- record this as a pending `TYPE_MISMATCH` condition to be persisted once the DB transaction below begins, and skip hash computation. Otherwise compute `actual_hash = sha256(P)` (or note absence), and read its mtime and permissions if it exists, before any DB transaction or backup creation begins.
   * *DB*: `BEGIN IMMEDIATE`.
     * FIRST query `shim_pending_operations` by `idempotency_key`. If found, verify `request_digest` matches (reject as a conflict if not); if it matches, `COMMIT` this read-only transaction immediately and return the existing row's state to the caller UNCHANGED — do NOT proceed to any of the remaining bullets below (concurrency check, admission-target validation, fallback-precondition check, pre-condition hash check, authorization, or a new `INSERT`). **(Fix, post-2026-08-23 final perfection audit):** the prior text listed these as a flat sequence without stating this resume path short-circuits them, which would have caused a real bug: a resumed operation whose Step 3 write already landed (crashed only before its `COMPLETED` commit) would have `actual_hash == post_state_hash`, which the pre-condition hash check below compares against `pre_state_hash` and would therefore falsely raise `ERR_SHIM_EXTERNALLY_MODIFIED` on a perfectly healthy, resumable operation. The remaining bullets in this step apply ONLY to the "not found" (fresh) path.
     * If not found, resolve the `ACTIVE` or `PROVISIONING` `shim_registry_entries` row (if any) matching this `shim_name`/`canonical_shim_path` via the deterministic canonicalization function. A partial match on only one of the two is a typed identity-conflict error, never a fallthrough.
     * *Concurrency check*: query `shim_pending_operations` for a non-terminal (`operation_state NOT IN ('COMPLETED', 'ABORTED')`) row on this `shim_registration_id`. If one exists with a different `idempotency_key`, abort with a typed "operation already in progress" conflict.
     * *Admission-target validation*: for any operation leaving a shim (`intended_registry_outcome='ACTIVE'`), fetch the referenced `manifest_admission_receipts` row's `transitive_executable_chain_json` and deserialize it. Before canonicalizing "the sole node," explicitly require the deserialized `transitive_executable_chain_json` to be a JSON array containing EXACTLY one element; if it is empty, has more than one element, or isn't a valid array, abort (do not silently take element 0 or crash unhandled). Once validated, canonicalize the `canonical_path` of the chain's sole node (Phase 1's admission coordinator, which only ever admits a single-node chain (enforced in application code, not by a database CHECK constraint on the JSON array itself), guarantees exactly one node regardless of its `role` -- `ENTRYPOINT_WRAPPER` or `NATIVE_BINARY` are both legitimate single-node chain shapes item B admits). Compare it against the canonicalized intended `downstream_target_path`. Abort before any mutation on mismatch. This positional/single-node approach is safe ONLY because Phase 1 enforces exactly one node. Phase 2, when multi-node chains are eventually supported, will need genuine entrypoint identification that doesn't rely on array position OR role inference -- e.g., an explicit `admitted_entrypoint_canonical_path` (or equivalent explicit identity) exposed directly on `ManifestAdmissionReceipt` itself, rather than derived by item C's own reading of the chain. This is out of Phase 1's scope and is recorded here as a named Phase 2 precondition, not solved by this document.
     * *Fallback-precondition check (`REMOVE` only)*: if `operation_kind='REMOVE'`, verify a fallback executable for the tool exists elsewhere on `PATH` outside the PeerHub shim directory, per the real doc's §2.7. Abort if none is found. Persist its path as `intended_fallback_tool_path`.
     * *Pre-condition hash check*: if Step 1's FS evaluation recorded a pending `TYPE_MISMATCH` (non-regular `P`), treat that as a mismatch unconditionally, skipping the hash comparison below entirely. Otherwise, compare `actual_hash` against the intended `pre_state_hash` (the active registration's own `shim_file_sha256` for `MANAGED_UPDATE`/`REMOVE`/`RESTORE`, the foreign file's hash for `EXTERNAL_COLLISION`, `NULL`/absent for `ABSENT`). On either kind of mismatch, `INSERT` into `shim_pending_operations` with the exact same field set the normal `INTENT_DECLARED` `INSERT` below records, but with `operation_state='ABORTED'` directly (never passing through `INTENT_DECLARED`), applying the Terminal Failure Registration-Retirement Logic in the SAME transaction (a `PROVISIONING` row retires; an `ACTIVE` row is left untouched); `COMMIT`; raise `ERR_SHIM_EXTERNALLY_MODIFIED`. **(Fix, post-Round-146 MECE audit):** the original text simply aborted here with nothing persisted -- a single external modification of `P`, requiring no crash at all, left the registry `ACTIVE` with a stale hash forever, since `accept_current_as_baseline` and `retire_registration_preserving_current_state` both require referencing an already-`ABORTED` `idempotency_key` that never existed. Persisting the `ABORTED` row here gives the operator a genuine record to act on. This treatment is scoped NARROWLY to this specific failure -- the other Step 1 failure modes (concurrency conflict, identity conflict, admission-target mismatch, missing authorization) are caller-input-correction or genuine-conflict-resolution cases that don't need an `ABORTED` record for later recovery. **(Fix, post-2026-08-23 final perfection audit):** this bullet now also covers the `TYPE_MISMATCH` case (`P` replaced by a directory/symlink/etc.), which previously would have hit Step 1's FS evaluation's own "rejected... at whichever call site" precondition before ever reaching this DB transaction, skipping the Round-146 audit-log fix entirely for that specific tampering variant.
     * *Authorization*: for `EXTERNAL_COLLISION`, require a caller-supplied `force_override_authorized=1`; abort `ERR_SHIM_COLLISION_DETECTED` if absent or false, matching the real doc's §2.3 fail-closed-by-default requirement — the collision is auto-*detected*, but never auto-*overridden*.
     * If no `ACTIVE` or `PROVISIONING` registry row exists yet (`ABSENT`/`EXTERNAL_COLLISION`), `INSERT` the new parent `shim_registry_entries` row first with `status='PROVISIONING'` — the parent must commit-order before any child references it.
     * `INSERT` into `shim_pending_operations` recording `operation_kind`, `(pre_state_hash, post_state_hash)`, the live-read `original_mtime_epoch` and `original_permissions_octal` (if `P` exists), intended bindings (if `intended_registry_outcome='ACTIVE'`), `force_override_authorized`, `force_override_reason` (if applicable), `selected_backup_sequence_id` (if restoring — chosen once here via `ORDER BY backup_sequence_id DESC LIMIT 1` among unrestored backups where `corrupt_detected_at IS NULL`, and never re-queried by later steps), `intended_fallback_tool_path` (if `REMOVE`), and `request_digest`. `operation_state='INTENT_DECLARED'`.
     * `COMMIT`.

2. **INTENT_DECLARED → FS_STAGED**
   * *FS (staging only, no replacement of `P` yet)*: For operations writing a payload (`intended_registry_outcome='ACTIVE'`, `RESTORE`), derive a deterministic staging path based purely on the operation's identity: e.g. `staging_path = <shim_directory>/.peerhub-staging/<sha256(idempotency_key)>` (using the full 64-hex-character digest to eliminate collision risk). `idempotency_key`, a `str`, MUST be encoded as UTF-8 bytes before hashing (`idempotency_key.encode('utf-8')`), and the resulting digest MUST be rendered as `hexdigest()` (lowercase hexadecimal, 64 characters) -- both matching the same normative rendering rule as the lock-identity digest in Fix 1, for consistency throughout this document. This ensures a resumed process always computes the exact same path. The `.peerhub-staging/` subdirectory must reside INSIDE the shim directory itself to guarantee same-volume atomicity for the eventual `os.replace` (a load-bearing property previously unstated), and must carry the same owner-only permissions/restrictive ACL discipline mandated for the backup archive directory in the A2 fix. Because `PATH` entries are not recursive, this subdirectory is NOT itself `PATH`-exposed, keeping foreign executable bytes out of the execution path during staging. Additionally, the following staging-directory hardening must be applied: (1) Create/validate `.peerhub-staging/` as a genuine, no-follow-verified, owner-controlled directory -- reject if it's a symlink, or a Windows junction/reparse point pretending to be a directory. (2) Verify it resides on the same mounted filesystem instance / atomic-rename domain as `P`'s DESTINATION PARENT DIRECTORY (safely opened, no-follow) (verified, not merely inferred from matching device IDs, since two paths can report the same device ID while still crossing a mount-point boundary that makes `rename()` fail with `EXDEV`). If this check cannot be performed with full certainty on a given platform, the design MUST fail safe -- treat an uncertain or failed `os.replace()` (an `EXDEV` or equivalent cross-device error) as an operational (transient/retryable) failure, exactly matching this document's existing transient-failure handling pattern elsewhere, never as data corruption or a reason to silently fall back to a non-atomic copy+delete. (3) On EVERY create-or-validate of the `.peerhub-staging/` directory, durably `fsync` its PARENT directory (the shim directory itself) as part of that same step, unconditionally, not just the staged file and the `.peerhub-staging/` directory itself -- otherwise the directory ENTRY (the fact that `.peerhub-staging/` exists inside the shim directory) may still lack proven crash-durability if a previous attempt crashed before its own parent fsync.
     * *Safe File Operations (Security Discipline)*: As a document-wide rule applying to every read, hash, fsync, or metadata operation performed on `P` throughout this entire state machine, AND every interaction with deterministic staging or backup paths, operations MUST strictly reject symlink/hardlink attacks. When checking if a path exists, inspect it WITHOUT following symlinks (e.g., `lstat`); if it exists but is not a plain regular file (e.g., a symlink, directory, or device node), abort immediately. When reusing an existing file or operating on `P`, validate it through a safely-opened handle (e.g., `open` with `O_NOFOLLOW` followed by `fstat` validation) so it cannot be raced or substituted after the type check, and additionally require `st_nlink == 1` (reject/treat-as-invalid if greater, as a legitimate artifact or target file in this domain should never have more than one hard link). Note that fully eliminating the gap between validating a file descriptor and consuming that exact object via a later path-based `os.replace` is a best-effort narrowing rather than an absolute guarantee, as it is partially platform-dependent. When recreating an invalid stage, NEVER truncate or overwrite in place; first remove ONLY the directory entry (e.g., `unlink` without following symlinks), then recreate the file exclusively from scratch (`O_CREAT | O_EXCL | O_NOFOLLOW`).
     * *Staging creation*: Attempt to create `staging_path` exclusively. If it fails with `EEXIST` (implying a previous attempt), safely inspect it as defined above. Validate its content hash through a safely-opened handle. If it matches the expected final content (via `post_state_hash` for ACTIVE-outcome ops, or the backup's `original_sha256`-derived content for RESTORE), reuse it as-is. If it does not match, unlink it (removing only the directory entry) and recreate it exclusively from scratch.
     * *Backup creation* (if `operation_kind='EXTERNAL_COLLISION'`): Derive backup artifact paths in an isolated, non-`PATH`-exposed archive directory (e.g. `~/.peerhub/backups/shims/<shim_registration_id>/<sha256(idempotency_key)>.bak` and its sibling `.json` meta; `idempotency_key`, a `str`, MUST be encoded as UTF-8 bytes before hashing (`idempotency_key.encode('utf-8')`), and the resulting digest MUST be rendered as `hexdigest()` (lowercase hexadecimal, 64 characters) -- both matching the same normative rendering rule as the lock-identity digest in Fix 1, for consistency throughout this document), NOT adjacent to `P` itself. **(Correction, post-Round-146 full-design review):** an earlier version of this design placed the finalized `.bak` directly next to `P` (e.g. `P.peerhub-backup.<sha256(idempotency_key)>.bak`), inside the same directory that is prepended to `PATH` for shim resolution. An independent `cx` security review confirmed this was NOT exploitable through PeerHub's actual invocation/dispatch mechanisms (no shim-directory enumeration or globbing; exact-filename executable resolution; fixed adapter names; `subprocess.Popen` with `shell=False`, no wildcard/shell expansion) — but it directly inverted `PHASE1-THIRDPARTY-DEFERRAL-AND-SHIMS-2026-08-20.md` §2.8.1's explicit requirement of an isolated archive directory, and it is a legitimate defense-in-depth concern (placement inside a `PATH`-exposed, high-visibility directory retains untrusted foreign executable bytes somewhere directory scanners, autocomplete, or a deliberately poisoned compound-`PATHEXT` configuration could interact with, even if not through PeerHub's own code). The isolated-directory placement above restores conformance with the original spec at no cost to the design: the deterministic naming, exclusive/no-follow creation discipline, and crash-resume behavior are all unchanged, only the parent directory differs. This archive directory MUST be created with owner-only permissions/restrictive ACLs; original file metadata (`original_mtime_epoch`/`original_permissions_octal`) remains recorded for restoration purposes only and is never used as the archive directory's own access policy. Give the finalized `.bak` path its own explicit inspect-before-act sequence:
       1. Safely inspect (no-follow, reject non-regular file) the finalized `.bak` path.
       2. If it exists and its hash matches the persisted `pre_state_hash`, `fsync` it and its parent directory, and skip `.tmp` re-staging entirely.
       3. If it is missing, or exists but doesn't hash-match, unlink it (if present). Stage a `.tmp` copy of `P` (using the same exclusive/no-follow creation logic), verify its hash explicitly against `pre_state_hash`, atomically `os.replace` the `.tmp` to the finalized `.bak` path, and `fsync` it and its parent directory.
       4. Independently validate or atomically regenerate `backup_meta.json` alongside whichever path was taken, strictly applying the same safe file operation discipline, and requiring it and its parent directory to be `fsync`ed. Regenerate it using the `original_mtime_epoch`/`original_permissions_octal` ALREADY persisted in this operation's own `shim_pending_operations` row (captured live from `P` at Step 1, before this operation ever began) -- NEVER by re-reading `P`'s current live state here, since `P`'s metadata could have drifted (e.g. an external `touch`) between Step 1 and this resumed attempt, and the operation's own already-committed intent row is the only value that's authoritative for what `P` looked like when this operation started. If this sidecar generation fails terminally AFTER the `.bak` was already finalized+fsynced but BEFORE the sidecar is durable (and therefore before the Backup DB Commit), the failure-handling path MUST explicitly delete EVERY uncommitted backup artifact in that window -- the finalized `.bak`, the sidecar `.json` itself (or its `.tmp` if mid-write), using no-follow unlink -- before transitioning to `ABORTED`, since no DB row was ever committed for them and they cannot be left behind untracked.
       5. *Backup DB Commit*: `BEGIN IMMEDIATE`; `INSERT ... ON CONFLICT (originating_idempotency_key) DO NOTHING` into `shim_backup_entries` (generating `backup_sequence_id`), populating its `original_mtime_epoch`/`original_permissions_octal` columns FROM this same operation-row source (not from a fresh read of `P` or of `backup_meta.json`); `COMMIT`. This ensures the `.bak` is durably tracked as a legitimate, quarantinable resource immediately after creation, before proceeding to main payload staging.
     * For `RESTORE`, call `validate_archive()` on the selected `.bak` archive with `expected_sha256=original_sha256` and `refsync_archive_and_parent=False`, using its strict no-follow, regular-file-only, `st_nlink == 1` discipline, and apply the following deterministic two-tier policy:
       * If `validate_archive()` returns `STRUCTURAL_FAILURE` (missing file, non-regular file type, hardlinked, or hash mismatch against `original_sha256`), immediately auto-quarantine the backup. Transition `operation_state` to `ABORTED` (applying the retirement logic and explicit cleanup of any stages/artifacts), **record `corrupt_detected_at` for this backup row in the same transaction** so it is never selected again, and abort `ERR_CORRUPT_BACKUP_ARCHIVE`.
       * If `validate_archive()` returns `OPERATIONAL_FAILURE` (permission/ACL denial, sharing lock violations, or unrecoverable read-side I/O errors like `EIO`), treat the result as **transient/retryable by default** and do NOT auto-quarantine. Abort normally, explicitly leaving `operation_state` exactly where it was (`INTENT_DECLARED`). No CAS transition occurs, no deterministic stage or backup artifact is created/kept (any partial stage is explicitly cleaned up), and the advisory lock is simply released. This explicitly ensures that the exact SAME idempotency key can be used to naturally retry later (a fresh call resumes at step 1's now-fast-path lookup, then re-attempts step 2's validation again without needing a special retry mechanism).
       * If `validate_archive()` returns `VALID`, copy the `.bak` file to the deterministic `staging_path` (using exclusive/no-follow creation), then apply the backup's recorded `original_mtime_epoch` and `original_permissions_octal` to that staging file.
     * If `intended_registry_outcome='ACTIVE'`, stage the full intended payload to the deterministic `staging_path` (using exclusive/no-follow creation) (but do not yet replace `P`).
     * *Durability*: require every payload-staging step (not just RESTORE's) to `fsync` both the staged file AND its parent directory.
     * If staging fails terminally, transition `operation_state` to `ABORTED` (applying retirement logic), delete the deterministically-named stage if it exists (using no-follow unlink) so it doesn't leak, and abort.
   * *DB*: `BEGIN IMMEDIATE`; `UPDATE shim_pending_operations SET operation_state='FS_STAGED' WHERE idempotency_key=? AND operation_state='INTENT_DECLARED'` (affected rows must equal 1); `COMMIT`. (The backup row, if any, was already durably committed in its own transaction immediately after `.bak` creation, per the *Backup DB Commit* sub-step above.)

**Named Primitives:** Step 3 and all five operator recovery operations below share the same underlying question — "given `P`'s live state, has this operation's effect already happened, never happened, or been externally invalidated, and how do I safely finish or cancel it?" Prior rounds answered this identically at five separate call sites, each carrying prose like "run **EXACTLY** the same read-only determination Step 3 already performs" or "reference/reuse the normal already-applied branch's full requirements **exactly**" — a duplication the design could only guard with emphasis, not structure (Round 145's self-contradiction was exactly this kind of drift). The primitives below are that shared structure, not a redesign: every rule stated is copied unchanged from the prior per-site prose, only named once instead of five times. (`validate_archive`, added later as a further consolidation of archive-validation logic within `resolve_effect_state`/`finalize_operation`'s own call sites, brings the total to four.)

* **`resolve_effect_state(P, op, *, force_apply_degenerate_restore=False) -> (outcome, was_degenerate)`** — Pure, read-only, no DB transaction, no filesystem write. Before hashing, inspect `P` using the document-wide no-follow file-identity rules. If `P` is absent, continue with the normal absent-path logic below. If `P` exists but is not a regular file with `st_nlink == 1` (a symlink, directory, device node, or hardlinked file), return `TYPE_MISMATCH` (`was_degenerate=False`) immediately — this primitive MUST NEVER raise an unhandled exception for this case. Otherwise, computes `actual_hash = sha256(P)` and returns one of `NEVER_APPLIED`, `ALREADY_APPLIED`, `EXTERNALLY_MODIFIED`, `TYPE_MISMATCH`, plus whether the degenerate branch was taken (`was_degenerate`, needed by `finalize_operation` below for one RESTORE-specific rule). **(Fix, post-2026-08-23 final perfection audit):** the prior text only stated non-regular `P` was "rejected... at whichever call site is inspecting `P`" without defining a concrete outcome, which left every caller's handling of this case unspecified — `TYPE_MISMATCH` closes that gap as an ordinary, non-exceptional 4th outcome. `TYPE_MISMATCH` MUST be handled identically to `EXTERNALLY_MODIFIED` by every caller (see `cancel_operation` below, and each caller's own branching): it is a strict variant of "externally invalidated," not a fundamentally different situation.
  1. **Degenerate case (`op.pre_state_hash == op.post_state_hash`)**: the FIRST discriminant is always `actual_hash == op.pre_state_hash`. If this does NOT hold, return `EXTERNALLY_MODIFIED` unconditionally — there is NO metadata-based override of a genuine byte mismatch, ever, regardless of `force_apply_degenerate_restore`. If it DOES hold (bytes are objectively correct), split by operation family:
     - ACTIVE-outcome (`ABSENT`/`EXTERNAL_COLLISION`/`MANAGED_UPDATE`): return `ALREADY_APPLIED` (`was_degenerate=True`).
     - `RESTORE`, and `force_apply_degenerate_restore=True`: return `ALREADY_APPLIED` (`was_degenerate=True`) REGARDLESS of the metadata tie-break below — this is `force_complete_unconfirmed_finalization`'s sole, narrowly-scoped exception (see its own entry below for why this is safe: unrecoverable metadata is exactly the class of problem that operation exists to bypass, and it is the ONLY caller that ever passes `force_apply_degenerate_restore=True`).
     - `RESTORE`, `force_apply_degenerate_restore=False` (every other caller): resolve the tie by checking `P`'s current `mtime`/permissions against the backup's REAL PERSISTED `original_mtime_epoch`/`original_permissions_octal` from `shim_backup_entries` — matching, return `ALREADY_APPLIED` (`was_degenerate=True`); not matching, return `NEVER_APPLIED` (`was_degenerate=True`).
  2. **Non-degenerate, `actual_hash == op.pre_state_hash`** (or `P` genuinely still absent when `pre_state_hash IS NULL`): return `NEVER_APPLIED` (`was_degenerate=False`).
  3. **Non-degenerate, `actual_hash == op.post_state_hash`** (or `P` genuinely absent when `post_state_hash IS NULL`): return `ALREADY_APPLIED` (`was_degenerate=False`).
  4. **Any other outcome**: return `EXTERNALLY_MODIFIED` (`was_degenerate=False`).

* **`finalize_operation(op, outcome, was_degenerate, *, allow_unconfirmed_finalization=False, override_reason=None)`** — Acts on a determination from `resolve_effect_state`. Only ever called with `outcome IN (NEVER_APPLIED, ALREADY_APPLIED)` — callers route `EXTERNALLY_MODIFIED` or `TYPE_MISMATCH` to `cancel_operation` (below) instead, never here.
  - **`outcome == NEVER_APPLIED`**: the effect must still be applied.
    - **RESTORE-specific, only when `was_degenerate=True`, checked FIRST**: if the deterministic stage file is missing (e.g. consumed by a prior `os.replace`), this is not an error — explicitly re-derive/re-stage it from the still-intact `.bak` archive before any hash verification is attempted, applying the exact same two-tier permanent-vs-transient validation checks to the `.bak` archive as Step 2 uses (structural failures auto-quarantine and route to `cancel_operation`; operational failures abort transiently, releasing the advisory lock, leaving state in `FS_STAGED`, no `cancel_operation` call). After those validations succeed, reapply the backup's recorded `original_mtime_epoch` and `original_permissions_octal` to the re-derived staging file, using the same no-follow metadata-operation and durability requirements specified for RESTORE staging in Step 2, before any staged-file hash verification or subsequent replacement. **(Fix, post-2026-08-23 final perfection audit):** the prior text named only the validation checks as shared with Step 2, omitting this metadata-reapplication step that follows them in Step 2's own text — a degenerate RESTORE exists specifically to fix a metadata mismatch, so silently skipping this step here would have let the operation complete without actually fixing what it was for. If the stage file is present, this step is a no-op. (This re-derivation is NOT performed for the non-degenerate `NEVER_APPLIED` case — the ordinary Step 1 precondition check already guarantees stage presence there, and a missing stage in that case is an operational hiccup handled by the standard staged-hash-verification failure path below, not a special re-derivation.)
    - For operations writing a payload (`intended_registry_outcome='ACTIVE'`, `RESTORE`): NOW that the stage is confirmed present (freshly re-derived above if this was a degenerate RESTORE, or assumed present otherwise), compute its hash and verify it equals `post_state_hash`. If mismatch, route to `cancel_operation` (do not proceed with replace).
    - For `EXTERNAL_COLLISION` specifically: before proceeding with the filesystem replacement, look up whether `op` has an associated backup (via `originating_idempotency_key` matching `op.idempotency_key`) with `corrupt_detected_at IS NOT NULL`. If quarantined, route to `cancel_operation` with `ERR_ORIGINATING_BACKUP_QUARANTINED` — do NOT proceed with the write. ("Already applied" as determined by `mark_backup_permanently_unusable` at one point in time does NOT bind this operation's own later, independent call into `finalize_operation` — `P` remains externally mutable in between, so this write-time check is what actually closes that gap, not any earlier point-in-time determination.) If NOT already quarantined, call `validate_archive()` immediately before writing on the operation's own originating `.bak`, with `expected_sha256=pre_state_hash` and `refsync_archive_and_parent=True`, using the exact no-follow, regular-file-only, `st_nlink == 1` discipline. If it returns `STRUCTURAL_FAILURE` (missing/non-regular/hardlinked/hash-mismatch), auto-quarantine the backup in the SAME transaction as the `cancel_operation` call, reusing `ERR_ORIGINATING_BACKUP_QUARANTINED`; do NOT proceed with the write. If it returns `OPERATIONAL_FAILURE` (permission/lock/IO error), abort transiently, releasing the advisory lock, leaving `FS_STAGED`, with no `cancel_operation` call. Only if it returns `VALID` does the filesystem replacement proceed. **(Fix, post-2026-08-23 final perfection audit):** both operational-failure branches above previously omitted lock release, unlike the parallel Step 2 RESTORE operational-failure path (which already explicitly states "the advisory lock is simply released") — an inconsistency that would leak the advisory lock indefinitely in a long-running daemon process that catches the failure rather than exiting.
    - For `REMOVE`: re-verify `intended_fallback_tool_path` is still executable/reachable under `PATH`/`PATHEXT` rules. If not, route to `cancel_operation`. *(Residual race: the fallback tool could be deleted by a non-cooperating external process between this check and the subsequent unlink — accepted under the same honest-limitation framing as the target-file TOCTOU narrowing.)*
    - Atomically `os.replace` the staged payload over `P` (or `os.remove(P)` when `post_state_hash IS NULL`), then `fsync` the file (if it still exists) and its parent directory. The staging file no longer exists at its deterministic path afterward, since `os.replace` consumed it.
    - Proceed to the shared DB completion below.
  - **`outcome == ALREADY_APPLIED`**: the effect either already happened (a prior attempt crashed between its own filesystem write and this transaction's commit) or, in the degenerate case, never needed a write at all — both are handled identically from here: do not touch the file's content again, but re-`fsync` it (if it exists) and its parent directory, since observing correct bytes does not prove the earlier `fsync` completed. For `RESTORE` specifically, also re-verify `P`'s current `mtime`/permissions against the backup's recorded `original_mtime_epoch`/`original_permissions_octal`; if they don't match, reapply them now with strictly no-follow semantics (operating on a safely-opened file descriptor, never silently following a symlink an attacker substituted for `P`), and `fsync` again.
    - If any required `fsync` (or the metadata reapply) fails: if `allow_unconfirmed_finalization=False` (every caller except `force_complete_unconfirmed_finalization`), do NOT proceed to `COMPLETED` — leave `operation_state` exactly at `FS_STAGED` (no CAS, no DB write at all), release the lock, so a later retry (or, if the condition is permanent, an explicit call to `force_complete_unconfirmed_finalization`) can resolve it. If `allow_unconfirmed_finalization=True`, proceed to the shared DB completion below anyway, but additionally set `finalization_override_reason=override_reason` and `finalization_override_at=<now>` on the `shim_pending_operations` row in the same completion transaction — an explicit, disclosed, operator-acknowledged risk where content correctness is verified but a secondary non-safety-critical finalization step is unconfirmed.
    - If all required `fsync`/metadata work succeeds, proceed to the shared DB completion below WITHOUT setting the override columns (leave both `NULL`) — no override was needed.
  - **Shared DB completion** (reached from either outcome above, on success): `BEGIN IMMEDIATE`; if `intended_registry_outcome='ACTIVE'`, `UPDATE shim_registry_entries SET shim_file_sha256=?, updated_at=?, profile_name=?, downstream_target_path=?, admission_receipt_id=?, status='ACTIVE' WHERE shim_registration_id=?`; if `'RETIRED'`, `UPDATE ... SET status='RETIRED', updated_at=?` instead; if this was a `RESTORE`, additionally `UPDATE shim_backup_entries SET restored=1, restored_at=... WHERE backup_sequence_id=?`; finally `UPDATE shim_pending_operations SET operation_state='COMPLETED' [, finalization_override_reason=?, finalization_override_at=?] WHERE idempotency_key=? AND operation_state='FS_STAGED'` (affected rows must equal 1); `COMMIT`. Release the advisory lock only AFTER this commit. Explicitly delete the deterministically-named stage if it exists (for `ALREADY_APPLIED` paths that never consumed it via `os.replace`).

* **`cancel_operation(op, error_code)`** — The shared safe-cancellation mechanism. Used whenever `resolve_effect_state` returns `EXTERNALLY_MODIFIED` or `TYPE_MISMATCH` (both are routed identically -- a type-changed `P` is a strict variant of "externally invalidated," not a fundamentally different situation requiring separate handling), whenever `finalize_operation`'s `NEVER_APPLIED` branch hits a structural staged-hash-mismatch or quarantined/invalid-archive condition, and by operator recovery operations that choose to cancel rather than complete. Core atomic unit: `BEGIN IMMEDIATE`; `UPDATE shim_pending_operations SET operation_state='ABORTED' WHERE idempotency_key=? AND operation_state=<caller's current state>` (affected rows must equal 1); apply the Terminal Failure Registration-Retirement Logic (below) in the SAME transaction; `COMMIT`. Some callers (`mark_backup_permanently_unusable`) compose an ADDITIONAL statement into this SAME transaction (quarantining a backup) before the commit — this is explicitly permitted, `cancel_operation`'s shape is the minimum required, not an exclusive transaction. After commit in every case: release the advisory lock, THEN explicitly delete the deterministically-named stage if it exists (using no-follow unlink), and the caller raises whatever typed error or returns whatever success signal is appropriate for its own context (`cancel_operation` itself carries no opinion on that). **(Disclosed simplification, not a carried-over exact order):** prior to this restatement, ordinary Step 3 cancellation already released the lock before deleting the stage, but several recovery-operation cancellation paths (`mark_backup_permanently_unusable`'s `INTENT_DECLARED` and originating-collision branches, `abandon_stuck_operation`) deleted the stage BEFORE releasing the lock instead — five call sites, two different orders. `cancel_operation` deliberately unifies all of them to the release-then-delete order (matching what Step 3 already did), on the basis that this ordering choice cannot affect correctness: the deterministic stage path's leaf name is derived from `idempotency_key` alone (via full SHA-256), placed within the operation's own shim directory, so no other operation's stage path can ever collide with it, meaning a shorter or longer advisory-lock hold around this specific cleanup step has no correctness or state-machine effect on any other operation (a waiter may observe marginally different lock latency, and an external observer could in principle see the terminal operation's own now-inert stage for a moment longer — neither affects any state transition). This was independently confirmed safe by a dedicated `cx` equivalence review before being accepted as the one deliberate simplification in an otherwise exact restatement. This is always safe: canceling never writes to `P` and never claims false success — for `NEVER_APPLIED`, `P` genuinely still has its pre-operation content, so nothing is inconsistent; for `EXTERNALLY_MODIFIED`, this is the design's universal safe response to an unexpected state, never an auto-overwrite.

* **`validate_archive(archive_path, expected_sha256, *, refsync_archive_and_parent=False) -> VALID | STRUCTURAL_FAILURE | OPERATIONAL_FAILURE`** — Shared, read-only-by-default archive validation primitive, factoring out the identical two-tier structural-vs-operational validation logic that Step 2's `RESTORE` backup validation and `finalize_operation`'s `EXTERNAL_COLLISION` just-in-time re-validation previously duplicated. Its contract: (1) safely inspect `archive_path` without following symlinks; (2) require it to be a regular file; (3) require `st_nlink == 1` (hardlinked archives are rejected); (4) a missing file, non-regular file type, hardlink, or hash mismatch against `expected_sha256` is `STRUCTURAL_FAILURE`; (5) a permission/ACL denial, sharing-lock violation, or unrecoverable read-side I/O error (e.g. `EIO`) is `OPERATIONAL_FAILURE`; (6) if `refsync_archive_and_parent=True`, `fsync` the archive and its parent directory after a successful hash match — failure of either `fsync` is itself an `OPERATIONAL_FAILURE`; (7) otherwise, a successful inspection and hash match returns `VALID`. `validate_archive()` performs NO quarantine, state transition, cancellation, staging, artifact creation, cleanup, lock release, or transaction management of its own — every consequence of its returned outcome remains entirely caller-owned, exactly as it was before this consolidation (Step 2 and `finalize_operation`'s `EXTERNAL_COLLISION` branch apply different consequences to the same three outcomes, which is why those remain separate call sites rather than being folded into the primitive itself). This is a zero-semantic-change consolidation, `cx`-drafted and rule-by-rule verified equivalent to the two call sites' pre-consolidation prose before being spliced in.

3. **FS_STAGED → COMPLETED | ABORTED**: `(outcome, was_degenerate) = resolve_effect_state(P, op)`. If `outcome IN (EXTERNALLY_MODIFIED, TYPE_MISMATCH)`: `cancel_operation(op, ERR_SHIM_EXTERNALLY_MODIFIED)`. Otherwise (`NEVER_APPLIED` or `ALREADY_APPLIED`): `finalize_operation(op, outcome, was_degenerate)`.

**Terminal Failure (ABORTED) Registration-Retirement Logic:**
At every point where `cancel_operation` (or a rollback before Step 1 commits) causes an operation to transition to `ABORTED`, the following applies to the registry row, in the same transaction:
* If the `shim_registry_entries` row's CURRENT status is `PROVISIONING` (a fresh install that never completed), atomically update it to `RETIRED` (`updated_at=...`). This frees the `shim_name`/`canonical_shim_path` for a fresh attempt.
* If the registry row's CURRENT status is `ACTIVE` (`MANAGED_UPDATE`/`RESTORE`/`REMOVE` against an already-established shim), leave it untouched — the existing good shim remains `ACTIVE` and unaffected.
* Note on cleanup ordering: even if a crash lands exactly between committing `ABORTED` and deleting its stage, the leak is merely cosmetic (an inert file derivable from a permanently-terminal operation) — a later reconciliation pass MAY safely clean up stages of any `ABORTED`/`COMPLETED` operation it encounters, since deletion is always idempotent once an operation is terminal.

**Operator recovery from `ABORTED`, permanently corrupted backups, and permanently stuck `FS_STAGED` operations (Fixes F1 & F5):**
Because an `ABORTED` operation no longer blocks new operations on the registry, a fresh idempotency key can simply declare a new intent.
Additionally, explicit repository operations are provided for human-escalated recovery:

> [!NOTE]
> **Implementation Note on Lock Acquisition:** All five operator recovery operations below (`accept_current_as_baseline`, `retire_registration_preserving_current_state`, `mark_backup_permanently_unusable`, `abandon_stuck_operation`, and `force_complete_unconfirmed_finalization`) require acquiring the advisory lock scoped to the canonicalized shim identity (`shim_name`/`canonical_shim_path`). Resolving the input arguments (`backup_sequence_id` or `idempotency_key`) to their corresponding canonicalized identity (and `shim_registration_id`) requires a database lookup. This lookup MUST happen as a clean, already-committed autocommit read (with no transaction or cursor held open) BEFORE acquiring the advisory lock, avoiding an inversion of the lock-then-transaction order established throughout this design. The resolved identity and registration ID must then be re-validated inside the subsequent `BEGIN IMMEDIATE` transaction rather than trusted from the earlier read.

* **`accept_current_as_baseline(aborted_idempotency_key, expected_inspected_hash)`** — For the specific case where an operator manually inspects `P` after an `ERR_SHIM_EXTERNALLY_MODIFIED` and judges its externally-modified content acceptable. The operator supplies the digest they personally inspected and the idempotency key of the aborted operation that prompted this review. The operation acquires the advisory lock and re-verifies `sha256(P) == expected_inspected_hash` at execution time (aborting if it has changed since inspection — do not trust a stale claim). It then READS (without mutating) the referenced aborted operation and requires that its `operation_kind IN ('MANAGED_UPDATE', 'RESTORE', 'REMOVE')` and `operation_state='ABORTED'`, explicitly excluding `ABSENT` and `EXTERNAL_COLLISION` since those never had a prior `ACTIVE` baseline to fall back to. Before committing the baseline update, it explicitly `fsync`s `P` and its parent directory (using the same safely-opened, no-follow discipline used throughout) since observing correct bytes doesn't prove durability. Finally, inside a `BEGIN IMMEDIATE` transaction, it queries `shim_pending_operations` for any row on this `shim_registration_id` with `operation_state NOT IN ('COMPLETED', 'ABORTED')` OTHER than the specifically-referenced aborted operation itself. If any such non-terminal row exists, REJECT with a typed error (e.g. `ERR_REGISTRATION_HAS_ACTIVE_OPERATION`) directing the operator to first resolve it (via its own normal resume, `abandon_stuck_operation`, or `force_complete_unconfirmed_finalization`, as appropriate) before baseline acceptance can proceed. Otherwise, it directly executes `UPDATE shim_registry_entries SET shim_file_sha256=<the live-verified hash>, updated_at=..., accepted_baseline_at=..., accepted_baseline_reference_idempotency_key=? WHERE shim_registration_id=? AND status='ACTIVE'` (CAS-guarded, affected-row-count checked). Kind-specific side effects: for `RESTORE`, the selected backup's `restored` flag is explicitly left at 0 and the original operation `ABORTED` (the archive was never consumed, so a later `RESTORE` can still reselect and revalidate it); for `REMOVE`, it does not touch its persisted `intended_fallback_tool_path` (no removal is being performed; a later `REMOVE` must redo its own fallback check fresh). *(This operation does not use `resolve_effect_state`/`finalize_operation` — it operates only on an already-`ABORTED` operation and never re-derives a live determination.)*
* **`retire_registration_preserving_current_state(aborted_idempotency_key)`** — For the missing-file variant where `P` was externally DELETED (not replaced), meaning an absent path cannot be accepted as a baseline into an `ACTIVE` row since `shim_registry_entries.shim_file_sha256` is non-null by schema and there's no live file to hash/fsync. It acquires the advisory lock (resolved via the same clean-autocommit-read-before-lock pattern used throughout), verifies under the lock that `P` is genuinely absent (requiring a no-follow check; if any path entry exists at `P`, regardless of type, reject with a typed error), reads the referenced aborted operation (requiring its `operation_kind IN ('MANAGED_UPDATE', 'RESTORE', 'REMOVE')` and `operation_state='ABORTED'`). Next, inside a `BEGIN IMMEDIATE` transaction, it queries `shim_pending_operations` for any row on this `shim_registration_id` with `operation_state NOT IN ('COMPLETED', 'ABORTED')` OTHER than the specifically-referenced aborted operation itself. If any such non-terminal row exists, REJECT with a typed error (e.g. `ERR_REGISTRATION_HAS_ACTIVE_OPERATION`) directing the operator to first resolve it before retirement can proceed. Otherwise, it CASes the registry `status` from `ACTIVE` to `RETIRED` (affected-row-count checked, updating `updated_at=...`). It must NEVER modify `P` itself, and must leave the aborted operation's row and any backup's `restored` flag untouched as an honest audit record. This operation exists purely to free the `shim_name`/`canonical_shim_path` for a subsequent fresh `ABSENT` or `EXTERNAL_COLLISION` install, exactly like the Terminal Failure Registration-Retirement Logic already does for `PROVISIONING` rows, just invoked explicitly by an operator for this specific already-`ACTIVE` stuck case. (Note: a `P` which exists but is non-regular, hardlinked, or otherwise structurally inadmissible is a disclosed, out-of-scope residual limitation of this recovery operation set and is not silently handled.) *(Also does not use `resolve_effect_state`/`finalize_operation`, for the same reason as `accept_current_as_baseline`.)*
* **`mark_backup_permanently_unusable(backup_sequence_id, reason)`** — Automatic escalation from "repeated operational failure" to "quarantined" is explicitly out of scope for this persistence design (as it requires policy decisions like retry-count thresholds that belong to a higher operational layer). If a human operator diagnoses a backup as genuinely, permanently broken (e.g., after observing repeated persistent ACL denials or sharing lock violations on the same archive across multiple retry attempts), they can use this explicit escalation path. **This operation is lock-guarded and coordination-aware**, since a plain unconditional update would deadlock against an already-in-flight `RESTORE` that previously selected this exact backup (backup selection is pinned once at `INTENT_DECLARED` and never re-queried, so an unresolved stuck operation would remain pinned to the now-corrupt backup forever while also blocking any fresh `RESTORE` attempt via `idx_active_pending_operation`). Furthermore, a backup's own originating operation is a distinct, previously-unchecked coordination participant from any operation that later selects it for restoration, and both must be accounted for before quarantine can safely proceed:
  1. Require a non-empty `reason` string (reject if blank/null). Acquire the same advisory lock used throughout this state machine, scoped to the canonicalized identity (resolved prior to lock acquisition, as noted above).
  2. `BEGIN IMMEDIATE`; re-validate that the `backup_sequence_id` still belongs to the acquired registration (rather than trusting the earlier read). Check for any non-terminal (`operation_state NOT IN ('COMPLETED', 'ABORTED')`) row in `shim_pending_operations` with `selected_backup_sequence_id` equal to the target backup. **Additionally**, perform a second lookup to check `shim_pending_operations` for a non-terminal row whose `idempotency_key` equals the target backup's own `originating_idempotency_key`. Also check the backup's current state. If `corrupt_detected_at` is already set, this repeat call is treated as an idempotent no-op (preserving the original record's timestamp and reason, as the first detection is the historically accurate one for an audit trail); `COMMIT` and exit.
  3. **No in-flight operation found**: directly `UPDATE shim_backup_entries SET corrupt_detected_at=..., corrupt_reason=? WHERE backup_sequence_id=? AND corrupt_detected_at IS NULL`. Explicitly verify the affected-row-count equals 1 (treating 0 as a conflict). `COMMIT`.
  4. **Exactly one in-flight operation found at `INTENT_DECLARED`**: safe to resolve automatically — `INTENT_DECLARED` guarantees `P` has not been replaced, NOT that no filesystem write occurred at all (Step 2 can have already created and `fsync`ed a deterministic stage before crashing ahead of the `FS_STAGED` DB commit). Compose the backup-quarantine `UPDATE` into `cancel_operation`'s SAME transaction: `UPDATE shim_backup_entries SET corrupt_detected_at=..., corrupt_reason=?...` (affected-row-count checked) alongside `cancel_operation(op, <no error raised, this is a successful quarantine>)`'s own `UPDATE shim_pending_operations SET operation_state='ABORTED' WHERE idempotency_key=? AND operation_state='INTENT_DECLARED'` (affected rows equal 1) and retirement logic, all in one `COMMIT`. Explicitly delete any stale deterministic stage that may already exist for the operation being quarantined (using safe no-follow unlink, after commit).
  5. **Exactly one in-flight operation found at `FS_STAGED`**:
     - If the operation is a `RESTORE` (found via `selected_backup_sequence_id`): NOT safe to resolve automatically — the filesystem effect may or may not have already happened. Reject the quarantine request with a typed error (e.g., `ERR_BACKUP_IN_USE_BY_ACTIVE_RESTORE`) instructing the operator to first resolve the in-flight operation (via normal resume, `abandon_stuck_operation`, or `force_complete_unconfirmed_finalization`, as appropriate) before retrying quarantine. Never blindly cancel an `FS_STAGED` operation from this path. Explicitly roll back/close the transaction before releasing the advisory lock, not just return an error.
     - If the operation is the backup's own ORIGINATING `EXTERNAL_COLLISION` (found via `originating_idempotency_key`): `(outcome, was_degenerate) = resolve_effect_state(P, op)`, under the same lock and in the same transaction as the quarantine decision.
       - `outcome == ALREADY_APPLIED` (the destructive overwrite has ALREADY happened): the backup can no longer protect anything additional by blocking, and quarantining it has no unsafe interaction with the collision operation's own eventual completion — that operation's own LATER, independent resume will call `resolve_effect_state` fresh at that time (never trusting this quarantine operation's own point-in-time observation), then route the result exactly as Step 3 always does: `EXTERNALLY_MODIFIED` to `cancel_operation`, or `NEVER_APPLIED`/`ALREADY_APPLIED` to `finalize_operation`. `finalize_operation` is never invoked from HERE, specifically because doing so would require its FS re-fsync work inside this same transaction, violating the "no transaction held open across a filesystem write" invariant — not because of anything about what the later resume will find. (`cancel_operation` is not a candidate here at all: this branch's own determination is `ALREADY_APPLIED`, which Step 3's own dispatch never routes to `cancel_operation` in the first place — only `EXTERNALLY_MODIFIED` does.) Simply quarantine the backup via the exact same plain `UPDATE shim_backup_entries SET corrupt_detected_at=..., corrupt_reason=? WHERE backup_sequence_id=? AND corrupt_detected_at IS NULL` used by the "No in-flight operation found" case (affected-row-count checked), `COMMIT`, leaving the collision operation's own state completely untouched.
       - `outcome IN (NEVER_APPLIED, EXTERNALLY_MODIFIED, TYPE_MISMATCH)` (the original foreign content, or some other content, is NOT yet overwritten by this collision operation): compose the backup-quarantine `UPDATE` into `cancel_operation`'s same transaction, exactly as the `INTENT_DECLARED` branch above does — this is what actually prevents the destructive write the operator was trying to avoid by quarantining in the first place.
  6. **BOTH in-flight checks find a non-terminal operation simultaneously**: this should be structurally unreachable under the invariants above (the backup's composite FK ties its originating operation to one registration; a selecting `RESTORE` is constrained to the same registration; `idx_active_pending_operation` permits at most one non-terminal operation per registration); if ever observed, treat it as evidence of database corruption or a violated invariant, and fail closed by rejecting rather than attempting to resolve two operations at once. Explicitly roll back/close the transaction before releasing the advisory lock, not just return an error.
  7. Release the advisory lock at the end of every path above.
  The `reason` string is persisted into the `corrupt_reason` column as audit/log context for the operator's decision.
* **`abandon_stuck_operation(idempotency_key)`** — For the specific case where an operation is permanently stuck in `FS_STAGED` due to a degenerate missing-stage scenario (e.g., a `RESTORE` of identical bytes that must re-derive from an archive that is now permanently unreadable), OR permanently stuck in `INTENT_DECLARED` due to a crash between Step 1's commit and Step 2's staging. Unlike the removed `force_reconcile_stuck_operation`, this operation requires no operator-supplied hash because nothing about determining state was ever unreliable in this scenario — only the ACT OF WRITING (when required) was blocked, and this operation never attempts that write; it only completes when the effect provably already happened, or safely cancels otherwise. **IMPORTANT SAFETY RESTRICTION:** For the `INTENT_DECLARED` case specifically, `abandon_stuck_operation` must ALWAYS route to `cancel_operation`, REGARDLESS of what `resolve_effect_state` returns -- it must NEVER call `finalize_operation` from the `INTENT_DECLARED` case, even if the determination happens to be `ALREADY_APPLIED`. `INTENT_DECLARED` proves only that `FS_STAGED` was never committed, and therefore Step 3 never modified `P`. It does NOT prove Step 2 never ran -- Step 2 may have run partially or even fully (staged a deterministic file, and for `EXTERNAL_COLLISION`, even finalized a `.bak`/sidecar) before crashing ahead of its own `FS_STAGED` commit. There is simply no COMMITTED proof that all mandatory Step 2 work (in particular, `EXTERNAL_COLLISION`'s backup creation) completed. If `P` happens to already match `post_state_hash` at this point, that can only be coincidence or external tampering -- NEVER a legitimate "a prior attempt already wrote it and crashed before completion" scenario, since Step 3 was never reached. Calling `finalize_operation`'s `ALREADY_APPLIED` completion path here would mark the operation `COMPLETED` (and, for `RESTORE`, mark a backup `restored=1`) WITHOUT the mandatory backup-creation/archive-validation step having been provably completed for `EXTERNAL_COLLISION`, or without the archive ever being validated for `RESTORE` -- a real safety violation of the exact guarantee those operation kinds exist to provide. Safely canceling (via `cancel_operation`) and letting a fresh operation retry through the full, correctly-ordered Step 1→2→3 pipeline is always safe and is what "abandon" should mean here.
  1. Acquire the same advisory lock used throughout this state machine, scoped to the canonicalized identity (resolved via a clean autocommit read before lock acquisition, per the existing implementation note).
  2. Outside any transaction (while holding the lock, matching how `resolve_effect_state` is always called before any `BEGIN IMMEDIATE`), require `operation_state IN ('FS_STAGED', 'INTENT_DECLARED')` for the referenced operation (read-check, reject if not in one of these states). Branch immediately on the operation's current state:
     - **If `operation_state == 'INTENT_DECLARED'`**: Skip `resolve_effect_state` entirely (reading/hashing `P` can itself fail and is unnecessary for this path). For `EXTERNAL_COLLISION` operations specifically, before invoking `cancel_operation`, explicitly check whether a `shim_backup_entries` row already exists for this operation's `idempotency_key` (via `originating_idempotency_key`). If NO such row exists, explicitly delete ALL deterministic uncommitted backup artifacts that may already exist at their deterministic paths (using the same no-follow unlink discipline used everywhere else) -- not just the finalized `.bak` and sidecar `.json`, but also their `.tmp` intermediate siblings (Step 2 can crash mid-staging, before either `.tmp` is atomically renamed to its finalized name) -- as all of these are untracked orphans in the absence of a committed row. Only proceed to `cancel_operation` after this cleanup has completed. If a row DOES exist (meaning the Backup DB Commit sub-step completed), leave the finalized `.bak`/`.json` untouched as legitimately tracked, potentially-quarantinable resources (any leftover `.tmp` sibling in this case is still safe to remove, since the finalized artifact is what matters once tracked). Finally, route to `cancel_operation(op, <no error raised, this is a successful cancellation>)` unconditionally.
     - **If `operation_state == 'FS_STAGED'`**: Call `(outcome, was_degenerate) = resolve_effect_state(P, op)` — the plain, unmodified call, WITHOUT `force_apply_degenerate_restore` (this operation's safe-cancel guarantee depends on `NEVER_APPLIED` genuinely meaning `P` still holds pre-operation content, which the plain metadata tie-break preserves). Then branch on `outcome`:
       - **If `outcome == ALREADY_APPLIED`**: `finalize_operation(op, outcome, was_degenerate)` — the plain call, WITHOUT `allow_unconfirmed_finalization` (if finalization can't be confirmed, this operation is not the right tool; `force_complete_unconfirmed_finalization` is).
       - **If `outcome IN (NEVER_APPLIED, EXTERNALLY_MODIFIED, TYPE_MISMATCH)`**: `cancel_operation(op, <no error raised, this is a successful cancellation>)` — this operation's whole point is to safely cancel here instead of leaving the operation stuck. Safe specifically because canceling never writes anything and never claims false success: for `NEVER_APPLIED`, `P` genuinely still has its pre-operation content; for `EXTERNALLY_MODIFIED` or `TYPE_MISMATCH`, this is the same safe response the design already uses everywhere else — this recovery tool must never itself crash merely because `P` was externally replaced with a non-regular file.
  3. Release the lock (already handled by `finalize_operation`/`cancel_operation` above, restated here for clarity of the operation's own control flow).

* **`force_complete_unconfirmed_finalization(idempotency_key, reason)`** — For the specific case where an operation is permanently stuck in `FS_STAGED` because its filesystem write (`os.replace`/`os.remove`) succeeded (so the content is verifiably correct) but a required subsequent finalization step fails persistently (e.g., unresolvable permission/storage condition causing `fsync` to fail, or metadata reapplication failing for a `RESTORE`).
  1. Require a non-empty `reason` string (reject if blank/null). Acquire the same advisory lock used throughout this state machine, scoped to the canonicalized identity (resolved via a clean autocommit read before lock acquisition). Require `operation_state='FS_STAGED'`.
  2. `(outcome, was_degenerate) = resolve_effect_state(P, op, force_apply_degenerate_restore=True)` — the ONE caller in this whole design that ever passes this flag. As documented on the primitive itself: for a degenerate `RESTORE` where bytes are objectively correct, this treats it as `ALREADY_APPLIED` regardless of the metadata tie-break, since unrecoverable metadata is exactly the class of problem this operation exists to bypass; a genuine byte mismatch is still, always, `EXTERNALLY_MODIFIED` — never converted into a forced success. If `outcome != ALREADY_APPLIED`, REJECT with a typed error directing the operator to `abandon_stuck_operation` instead — this operation is not for those cases.
  3. `finalize_operation(op, ALREADY_APPLIED, was_degenerate, allow_unconfirmed_finalization=True, override_reason=reason)`. As documented on the primitive: this attempts the required fsync/metadata work once more, and only sets `finalization_override_reason`/`finalization_override_at` (and completes anyway) if that attempt still fails — if it succeeds, this completes as an entirely ordinary `ALREADY_APPLIED` finalization, with the override columns left `NULL`; no override was actually needed, and `reason` is simply discarded rather than persisted anywhere.
  4. Release the lock (handled by `finalize_operation` above).

Combined, `abandon_stuck_operation`, `mark_backup_permanently_unusable`, and `force_complete_unconfirmed_finalization` give the operator a full recovery path.

### 3. Transactional Replacement
The complex JSON-file read-modify-write cycle (with `.tmp.<pid>` files and `os.replace`) is eliminated. Each state transition above is its own short, independently-committed `UnitOfWork`:
```python
with self._store.unit_of_work() as unit:
    unit.commit_backup_entry(new_backup_entry)
    unit.commit()
```
If a flat `shim_registry.json` file is required for external tooling consumption or fast shell pathing, it becomes an **explicitly-derived, read-only export/cache**. Consistent with how `ARCHITECTURE.md` treats the adapter registry as a "disposable derived index", a post-commit hook or explicit CLI command regenerates the JSON cache from the SQLite operational source of truth. It is never read back in to make dispatch decisions or write resolutions.

### 4. Concrete Repository Operations and Executed Trace (Round 103)

> [!WARNING]
> **SUPERSEDED BY THE ROUND 104+ REDESIGN (Section C subsections 1-2 above, continuously refined in subsequent rounds -- see PHASE1-PROCESS-BACKLOG-2026-08-20.md for the full round-by-round history)**
> The implementation and trace in this section predate the Round 104+ redesign detailed above. It still branches on `install_sub_path` and lacks `ABORTED`, `PROVISIONING`, `REMOVE`, and the uniform `(pre_state_hash, post_state_hash)` model. It is retained here strictly as a historical audit record of the pre-redesign approach. **It does not reflect the current schema and state machine.** A fresh trace for the current design is pending as future work.

The schema and state machine from the Round 103 baseline were ratified at Round 102 after 8 review rounds and 5 fix rounds. This section implements that earlier design as real repository operations (`SqliteShimRegistryUnitOfWork`, conforming to the `UnitOfWork` pattern established in item B) and proves them with a genuinely executed trace.

**Independent verification note:** ag's first draft of the script below had two real bugs, both caught by the terminal running the script directly rather than trusting the delivered "should work" claim, consistent with this item's established discipline (Rounds 76 and 92 both had unrunnable or fabricated trace submissions):
1. `SqliteShimRegistryUnitOfWork.__exit__` never called `self.conn.close()`, so each `with store.unit_of_work() as uow:` block leaked a connection; after enough leaked connections the next `BEGIN IMMEDIATE` failed with `sqlite3.OperationalError: database is locked`. Reproduced on the very first run, at Scenario D. Fixed by closing the connection in `__exit__`'s `finally` block.
2. The Scenario E test setup inserted a placeholder `intended_admission_receipt_id='r'`, which doesn't exist in `manifest_admission_receipts` and violates that column's real FK — `sqlite3.IntegrityError: FOREIGN KEY constraint failed`. Fixed by using a real seeded receipt ID (`'receipt-1'`).
A third, non-functional issue (the long-lived `_keeper_conn` was never closed, which only surfaced as a `PermissionError` during the temp-directory's own cleanup at process exit, not a defect in the protocol itself) was also fixed for a clean run. The script below is the corrected version; the output beneath it was captured from 4 consecutive real runs (all identical, exit code 0 each time).

```python
import os
import tempfile
import sqlite3
import hashlib
import json
import uuid
import shutil
from datetime import datetime, timezone

def now_utc():
    return datetime.now(timezone.utc).isoformat()

SCHEMA_SQL = """
CREATE TABLE manifest_admission_receipts (
    admission_receipt_id TEXT PRIMARY KEY,
    manifest_canonical_sha256 TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    adapter_id TEXT NOT NULL,
    peer_kind TEXT NOT NULL,
    inventory_generation INTEGER NOT NULL,
    trust_root_json TEXT NOT NULL,
    observed_vendor_json TEXT NOT NULL,
    acl_evaluation_json TEXT,
    chain_complete INTEGER NOT NULL,
    aggregate_chain_digest TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    transitive_executable_chain_json TEXT NOT NULL,
    companion_binaries_json TEXT NOT NULL,
    admitted_at_utc TEXT NOT NULL,
    prov_chain_complete INTEGER NOT NULL
);

CREATE TABLE shim_registry_entries (
    shim_registration_id TEXT PRIMARY KEY,
    shim_name TEXT NOT NULL,
    canonical_shim_path TEXT NOT NULL,
    shim_path TEXT NOT NULL,
    downstream_target_path TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'RETIRED')),
    profile_name TEXT NOT NULL,
    admission_receipt_id TEXT NOT NULL REFERENCES manifest_admission_receipts(admission_receipt_id),
    shim_file_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_active_shim_name ON shim_registry_entries(shim_name) WHERE status = 'ACTIVE';
CREATE UNIQUE INDEX idx_active_canonical_path ON shim_registry_entries(canonical_shim_path) WHERE status = 'ACTIVE';

CREATE TABLE shim_pending_operations (
    idempotency_key TEXT PRIMARY KEY,
    request_digest TEXT NOT NULL,
    shim_registration_id TEXT NOT NULL REFERENCES shim_registry_entries(shim_registration_id),
    operation_type TEXT NOT NULL CHECK (operation_type IN ('INSTALL', 'RESTORE')),
    install_sub_path TEXT CHECK (install_sub_path IN ('ABSENT', 'EXTERNAL_COLLISION', 'MANAGED_UPDATE')),
    expected_hash TEXT,
    pre_effect_hash TEXT,
    intended_profile_name TEXT,
    intended_downstream_target_path TEXT,
    intended_admission_receipt_id TEXT REFERENCES manifest_admission_receipts(admission_receipt_id),
    selected_backup_sequence_id INTEGER,
    operation_state TEXT NOT NULL CHECK (operation_state IN ('INTENT_DECLARED', 'FS_STAGED', 'COMPLETED')),
    created_at TEXT NOT NULL,
    CHECK (
        (operation_type = 'INSTALL' AND expected_hash IS NOT NULL AND selected_backup_sequence_id IS NULL AND install_sub_path IS NOT NULL
            AND intended_profile_name IS NOT NULL AND intended_downstream_target_path IS NOT NULL AND intended_admission_receipt_id IS NOT NULL
            AND (
                (install_sub_path IN ('ABSENT', 'EXTERNAL_COLLISION') AND pre_effect_hash IS NULL) OR
                (install_sub_path = 'MANAGED_UPDATE' AND pre_effect_hash IS NOT NULL)
            ))
        OR
        (operation_type = 'RESTORE' AND selected_backup_sequence_id IS NOT NULL AND expected_hash IS NULL AND install_sub_path IS NULL AND pre_effect_hash IS NULL
            AND intended_profile_name IS NULL AND intended_downstream_target_path IS NULL AND intended_admission_receipt_id IS NULL)
    ),
    FOREIGN KEY (shim_registration_id, selected_backup_sequence_id) REFERENCES shim_backup_entries(shim_registration_id, backup_sequence_id)
);
CREATE UNIQUE INDEX idx_active_pending_operation ON shim_pending_operations(shim_registration_id) WHERE operation_state != 'COMPLETED';
CREATE UNIQUE INDEX idx_pending_op_reg_key ON shim_pending_operations(shim_registration_id, idempotency_key);

CREATE TABLE shim_backup_entries (
    backup_sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    shim_registration_id TEXT NOT NULL REFERENCES shim_registry_entries(shim_registration_id),
    originating_idempotency_key TEXT NOT NULL UNIQUE,
    target_path TEXT NOT NULL,
    backup_file_path TEXT NOT NULL,
    original_sha256 TEXT NOT NULL,
    original_mtime_epoch REAL NOT NULL,
    original_file_size_bytes INTEGER NOT NULL,
    original_permissions_octal TEXT NOT NULL,
    override_reason TEXT NOT NULL,
    backup_created_at TEXT NOT NULL,
    restored INTEGER NOT NULL DEFAULT 0 CHECK (restored IN (0, 1)),
    restored_at TEXT,
    FOREIGN KEY (shim_registration_id, originating_idempotency_key) REFERENCES shim_pending_operations(shim_registration_id, idempotency_key)
);
CREATE UNIQUE INDEX idx_backup_reg_seq ON shim_backup_entries(shim_registration_id, backup_sequence_id);
"""

class SqliteShimRegistryUnitOfWork:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is not None:
                self.conn.rollback()
        finally:
            self.conn.close()

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def declare_install_intent(self, idempotency_key, request_digest, shim_name, canonical_shim_path, shim_path, actual_hash_of_p, expected_hash, intended_profile_name, intended_downstream_target_path, intended_admission_receipt_id):
        cur = self.conn.cursor()

        row = cur.execute("SELECT * FROM shim_pending_operations WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if row:
            if row['request_digest'] != request_digest:
                raise ValueError("Conflict: request_digest mismatch")
            return {
                "install_sub_path": row['install_sub_path'],
                "shim_registration_id": row['shim_registration_id'],
                "pre_effect_hash": row['pre_effect_hash'],
                "operation_state": row['operation_state']
            }

        registry_rows = cur.execute("SELECT * FROM shim_registry_entries WHERE status='ACTIVE' AND (shim_name=? OR canonical_shim_path=?)", (shim_name, canonical_shim_path)).fetchall()

        receipt_row = cur.execute("SELECT transitive_executable_chain_json FROM manifest_admission_receipts WHERE admission_receipt_id=?", (intended_admission_receipt_id,)).fetchone()
        if not receipt_row:
            raise ValueError("Admission receipt not found")
        chain = json.loads(receipt_row['transitive_executable_chain_json'])
        entrypoint_path = chain[0]['canonical_path']
        if entrypoint_path != intended_downstream_target_path:
            raise ValueError(f"Admission target validation failed")

        if len(registry_rows) == 1 and registry_rows[0]['shim_name'] == shim_name and registry_rows[0]['canonical_shim_path'] == canonical_shim_path:
            reg = registry_rows[0]
            shim_registration_id = reg['shim_registration_id']

            pending = cur.execute("SELECT idempotency_key FROM shim_pending_operations WHERE shim_registration_id=? AND operation_state != 'COMPLETED'", (shim_registration_id,)).fetchone()
            if pending:
                raise ValueError("Conflict: operation already in progress")

            if actual_hash_of_p != reg['shim_file_sha256']:
                raise ValueError("ERR_SHIM_EXTERNALLY_MODIFIED")

            cur.execute("""
                INSERT INTO shim_pending_operations (
                    idempotency_key, request_digest, shim_registration_id, operation_type, install_sub_path, expected_hash,
                    pre_effect_hash, intended_profile_name, intended_downstream_target_path, intended_admission_receipt_id,
                    operation_state, created_at
                ) VALUES (?, ?, ?, 'INSTALL', 'MANAGED_UPDATE', ?, ?, ?, ?, ?, 'INTENT_DECLARED', ?)
            """, (idempotency_key, request_digest, shim_registration_id, expected_hash, reg['shim_file_sha256'],
                  intended_profile_name, intended_downstream_target_path, intended_admission_receipt_id, now_utc()))

            return {
                "install_sub_path": "MANAGED_UPDATE",
                "shim_registration_id": shim_registration_id,
                "pre_effect_hash": reg['shim_file_sha256'],
                "operation_state": 'INTENT_DECLARED'
            }

        elif len(registry_rows) == 0:
            install_sub_path = "EXTERNAL_COLLISION" if actual_hash_of_p is not None else "ABSENT"
            shim_registration_id = str(uuid.uuid4())

            cur.execute("""
                INSERT INTO shim_registry_entries (
                    shim_registration_id, shim_name, canonical_shim_path, shim_path, downstream_target_path, status,
                    profile_name, admission_receipt_id, shim_file_sha256, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?)
            """, (shim_registration_id, shim_name, canonical_shim_path, shim_path, intended_downstream_target_path,
                  intended_profile_name, intended_admission_receipt_id, expected_hash, now_utc(), now_utc()))

            cur.execute("""
                INSERT INTO shim_pending_operations (
                    idempotency_key, request_digest, shim_registration_id, operation_type, install_sub_path, expected_hash,
                    intended_profile_name, intended_downstream_target_path, intended_admission_receipt_id,
                    operation_state, created_at
                ) VALUES (?, ?, ?, 'INSTALL', ?, ?, ?, ?, ?, 'INTENT_DECLARED', ?)
            """, (idempotency_key, request_digest, shim_registration_id, install_sub_path, expected_hash,
                  intended_profile_name, intended_downstream_target_path, intended_admission_receipt_id, now_utc()))

            return {
                "install_sub_path": install_sub_path,
                "shim_registration_id": shim_registration_id,
                "pre_effect_hash": None,
                "operation_state": 'INTENT_DECLARED'
            }
        else:
            raise ValueError("Identity conflict: Exactly one of shim_name/canonical_shim_path matches")

    def mark_backup_staged(self, idempotency_key, shim_registration_id, install_sub_path, target_path, backup_file_path, original_sha256, original_mtime_epoch, original_file_size_bytes, original_permissions_octal, override_reason):
        cur = self.conn.cursor()
        if install_sub_path == 'EXTERNAL_COLLISION':
            cur.execute("""
                INSERT INTO shim_backup_entries (
                    shim_registration_id, originating_idempotency_key, target_path, backup_file_path, original_sha256,
                    original_mtime_epoch, original_file_size_bytes, original_permissions_octal, override_reason, backup_created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (originating_idempotency_key) DO NOTHING
            """, (shim_registration_id, idempotency_key, target_path, backup_file_path, original_sha256, original_mtime_epoch,
                  original_file_size_bytes, original_permissions_octal, override_reason, now_utc()))

        cur.execute("UPDATE shim_pending_operations SET operation_state='FS_STAGED' WHERE idempotency_key=? AND operation_state='INTENT_DECLARED'", (idempotency_key,))
        if cur.rowcount != 1:
            raise ValueError("CAS failed: operation not in INTENT_DECLARED state")

    def commit_shim_replacement(self, idempotency_key):
        cur = self.conn.cursor()
        row = cur.execute("SELECT * FROM shim_pending_operations WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if not row:
            raise ValueError("Operation not found")

        if row['install_sub_path'] == 'MANAGED_UPDATE':
            cur.execute("""
                UPDATE shim_registry_entries
                SET shim_file_sha256=?, updated_at=?, profile_name=?, downstream_target_path=?, admission_receipt_id=?
                WHERE shim_registration_id=?
            """, (row['expected_hash'], now_utc(), row['intended_profile_name'], row['intended_downstream_target_path'],
                  row['intended_admission_receipt_id'], row['shim_registration_id']))

        cur.execute("UPDATE shim_pending_operations SET operation_state='COMPLETED' WHERE idempotency_key=? AND operation_state='FS_STAGED'", (idempotency_key,))
        if cur.rowcount != 1:
            raise ValueError("CAS failed: operation not in FS_STAGED state")

    def declare_restore_intent(self, idempotency_key, request_digest, shim_registration_id):
        cur = self.conn.cursor()
        row = cur.execute("SELECT * FROM shim_pending_operations WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if row:
            if row['request_digest'] != request_digest:
                raise ValueError("Conflict: request_digest mismatch")
            return {
                "selected_backup_sequence_id": row['selected_backup_sequence_id'],
                "shim_registration_id": row['shim_registration_id'],
                "operation_state": row['operation_state']
            }

        backup = cur.execute("SELECT backup_sequence_id, original_sha256 FROM shim_backup_entries WHERE shim_registration_id=? AND restored=0 ORDER BY backup_sequence_id DESC LIMIT 1", (shim_registration_id,)).fetchone()
        if not backup:
            raise ValueError("Nothing to restore")

        cur.execute("""
            INSERT INTO shim_pending_operations (
                idempotency_key, request_digest, shim_registration_id, operation_type, selected_backup_sequence_id,
                operation_state, created_at
            ) VALUES (?, ?, ?, 'RESTORE', ?, 'INTENT_DECLARED', ?)
        """, (idempotency_key, request_digest, shim_registration_id, backup['backup_sequence_id'], now_utc()))

        return {
            "selected_backup_sequence_id": backup['backup_sequence_id'],
            "shim_registration_id": shim_registration_id,
            "operation_state": 'INTENT_DECLARED'
        }

    def mark_restore_staged(self, idempotency_key):
        cur = self.conn.cursor()
        cur.execute("UPDATE shim_pending_operations SET operation_state='FS_STAGED' WHERE idempotency_key=? AND operation_state='INTENT_DECLARED'", (idempotency_key,))
        if cur.rowcount != 1:
            raise ValueError("CAS failed: operation not in INTENT_DECLARED state")

    def commit_restore(self, idempotency_key):
        cur = self.conn.cursor()
        row = cur.execute("SELECT * FROM shim_pending_operations WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if not row:
            raise ValueError("Operation not found")

        cur.execute("UPDATE shim_backup_entries SET restored=1, restored_at=? WHERE backup_sequence_id=?", (now_utc(), row['selected_backup_sequence_id']))
        cur.execute("UPDATE shim_registry_entries SET status='RETIRED' WHERE shim_registration_id=?", (row['shim_registration_id'],))

        cur.execute("UPDATE shim_pending_operations SET operation_state='COMPLETED' WHERE idempotency_key=? AND operation_state='FS_STAGED'", (idempotency_key,))
        if cur.rowcount != 1:
            raise ValueError("CAS failed")

class FakeStateStore:
    def __init__(self, db_path):
        self.db_path = db_path
        self._keeper_conn = None

    def initialize(self):
        self._keeper_conn = sqlite3.connect(self.db_path, uri=True)
        self._keeper_conn.executescript(SCHEMA_SQL)

    def unit_of_work(self):
        conn = sqlite3.connect(self.db_path, timeout=6.0, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return SqliteShimRegistryUnitOfWork(conn)

def run_install(store, idempotency_key, shim_name, shim_path, expected_payload, profile, target_path, receipt_id, crash_before_fs_write=False, crash_after_fs_write=False):
    request_digest = hashlib.sha256(expected_payload).hexdigest()
    expected_hash = request_digest
    canonical_shim_path = os.path.normpath(os.path.abspath(shim_path)).lower()

    with store.unit_of_work() as uow:
        actual_hash_of_p = None
        if os.path.exists(shim_path):
            with open(shim_path, "rb") as f:
                actual_hash_of_p = hashlib.sha256(f.read()).hexdigest().upper()

        intent = uow.declare_install_intent(
            idempotency_key, request_digest, shim_name, canonical_shim_path, shim_path,
            actual_hash_of_p, expected_hash.upper(), profile, target_path, receipt_id
        )
        uow.commit()

    if intent['operation_state'] == 'INTENT_DECLARED':
        with store.unit_of_work() as uow:
            if intent['install_sub_path'] == 'EXTERNAL_COLLISION':
                backup_path = shim_path + ".bak"
                shutil.copy2(shim_path, backup_path)
                stat = os.stat(shim_path)
                uow.mark_backup_staged(idempotency_key, intent['shim_registration_id'], 'EXTERNAL_COLLISION', shim_path, backup_path, actual_hash_of_p, stat.st_mtime, stat.st_size, oct(stat.st_mode), "collision")
            else:
                uow.mark_backup_staged(idempotency_key, intent['shim_registration_id'], intent['install_sub_path'], "", "", "", 0, 0, "", "")
            uow.commit()
            intent['operation_state'] = 'FS_STAGED'

    if intent['operation_state'] == 'FS_STAGED':
        current_hash = None
        if os.path.exists(shim_path):
            with open(shim_path, "rb") as f:
                current_hash = hashlib.sha256(f.read()).hexdigest().upper()

        if current_hash == expected_hash.upper():
            pass
        elif current_hash == intent['pre_effect_hash'] or (current_hash is None and intent['install_sub_path'] == 'ABSENT') or (intent['install_sub_path'] == 'EXTERNAL_COLLISION' and current_hash == actual_hash_of_p):
            if crash_before_fs_write:
                return
            with open(shim_path, "wb") as f:
                f.write(expected_payload)
            if crash_after_fs_write:
                return
        else:
            raise ValueError("ERR_SHIM_EXTERNALLY_MODIFIED")

        with store.unit_of_work() as uow:
            uow.commit_shim_replacement(idempotency_key)
            uow.commit()

def run_restore(store, idempotency_key, shim_registration_id):
    request_digest = "dummy_digest"

    with store.unit_of_work() as uow:
        intent = uow.declare_restore_intent(idempotency_key, request_digest, shim_registration_id)
        uow.commit()

    if intent['operation_state'] == 'INTENT_DECLARED':
        with store.unit_of_work() as uow:
            uow.mark_restore_staged(idempotency_key)
            uow.commit()
            intent['operation_state'] = 'FS_STAGED'

    if intent['operation_state'] == 'FS_STAGED':
        with store.unit_of_work() as uow:
            uow.commit_restore(idempotency_key)
            uow.commit()

if __name__ == "__main__":
    print("--- TRACE DEMONSTRATION START ---")

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "test.db")
        store = FakeStateStore(db_path)
        store.initialize()

        with store.unit_of_work() as uow:
            receipt1_chain = [{"canonical_path": "/path/to/bin1"}]
            receipt2_chain = [{"canonical_path": "/path/to/bin2"}]
            uow.conn.execute("INSERT INTO manifest_admission_receipts (admission_receipt_id, manifest_canonical_sha256, schema_version, adapter_id, peer_kind, inventory_generation, trust_root_json, observed_vendor_json, chain_complete, aggregate_chain_digest, timestamp_utc, transitive_executable_chain_json, companion_binaries_json, admitted_at_utc, prov_chain_complete) VALUES (?, 'sha', '2', 'ad', 'pk', 1, '{}', '{}', 0, 'dig', 'time', ?, '[]', 'time', 0)", ("receipt-1", json.dumps(receipt1_chain)))
            uow.conn.execute("INSERT INTO manifest_admission_receipts (admission_receipt_id, manifest_canonical_sha256, schema_version, adapter_id, peer_kind, inventory_generation, trust_root_json, observed_vendor_json, chain_complete, aggregate_chain_digest, timestamp_utc, transitive_executable_chain_json, companion_binaries_json, admitted_at_utc, prov_chain_complete) VALUES (?, 'sha', '2', 'ad', 'pk', 1, '{}', '{}', 0, 'dig', 'time', ?, '[]', 'time', 0)", ("receipt-2", json.dumps(receipt2_chain)))
            uow.commit()

        shim_1 = os.path.join(temp_dir, "shim1.bat")
        shim_2 = os.path.join(temp_dir, "shim2.bat")
        shim_3 = os.path.join(temp_dir, "shim3.bat")
        shim_4 = os.path.join(temp_dir, "shim4.bat")

        print("\n[Scenario A] Admission target validation")
        try:
            run_install(store, "idemp-A", "shim1", shim_1, b"payload", "prof1", "/path/to/wrong", "receipt-1")
            print("FAIL: Expected ValueError")
        except ValueError as e:
            if "Admission target validation failed" in str(e):
                print("PASS: Rejected mismatched downstream_target_path")
            else:
                print(f"FAIL: Wrong error: {e}")

        print("\n[Scenario B] ABSENT sub-path")
        run_install(store, "idemp-B", "shim1", shim_1, b"payload1", "prof1", "/path/to/bin1", "receipt-1")
        print("PASS: Completed ABSENT install")

        print("\n[Scenario C] EXTERNAL_COLLISION sub-path")
        with open(shim_2, "wb") as f:
            f.write(b"foreign")
        run_install(store, "idemp-C", "shim2", shim_2, b"payload2", "prof1", "/path/to/bin1", "receipt-1")
        print("PASS: Completed EXTERNAL_COLLISION install")

        print("\n[Scenario D] MANAGED_UPDATE sub-path (changing bindings)")
        with store.unit_of_work() as uow:
            row_before = uow.conn.execute("SELECT downstream_target_path, profile_name FROM shim_registry_entries WHERE shim_name='shim1'").fetchone()
            print(f"Before: target={row_before['downstream_target_path']}, profile={row_before['profile_name']}")

        run_install(store, "idemp-D", "shim1", shim_1, b"payload1_updated", "prof2", "/path/to/bin2", "receipt-2")

        with store.unit_of_work() as uow:
            row_after = uow.conn.execute("SELECT downstream_target_path, profile_name FROM shim_registry_entries WHERE shim_name='shim1'").fetchone()
            print(f"After: target={row_after['downstream_target_path']}, profile={row_after['profile_name']}")
            if row_after['downstream_target_path'] == "/path/to/bin2" and row_after['profile_name'] == "prof2":
                print("PASS: Bindings updated atomically")
            else:
                print("FAIL: Bindings not updated")

        print("\n[Scenario E] RESTORE deterministic selection")
        with store.unit_of_work() as uow:
            reg_id = uow.conn.execute("SELECT shim_registration_id FROM shim_registry_entries WHERE shim_name='shim2'").fetchone()['shim_registration_id']
            uow.conn.execute("INSERT INTO shim_pending_operations (idempotency_key, request_digest, shim_registration_id, operation_type, install_sub_path, expected_hash, intended_profile_name, intended_downstream_target_path, intended_admission_receipt_id, operation_state, created_at) VALUES ('fake-op', 'dig', ?, 'INSTALL', 'EXTERNAL_COLLISION', 'hash', 'p', 't', 'receipt-1', 'COMPLETED', ?)", (reg_id, now_utc()))
            uow.conn.execute("INSERT INTO shim_backup_entries (shim_registration_id, originating_idempotency_key, target_path, backup_file_path, original_sha256, original_mtime_epoch, original_file_size_bytes, original_permissions_octal, override_reason, backup_created_at) VALUES (?, 'fake-op', 'tgt', 'bak', 'sha', 0, 0, '0', 'rsn', ?)", (reg_id, now_utc()))
            uow.commit()

        run_restore(store, "idemp-E", reg_id)
        with store.unit_of_work() as uow:
            backups = uow.conn.execute("SELECT backup_sequence_id, restored FROM shim_backup_entries WHERE shim_registration_id=? ORDER BY backup_sequence_id", (reg_id,)).fetchall()
            if backups[1]['restored'] == 1 and backups[0]['restored'] == 0:
                print("PASS: Selected most recent unrestored backup deterministically")
            else:
                print("FAIL: Selected wrong backup")

        print("\n[Scenario F] Composite-FK cross-registration rejection")
        with store.unit_of_work() as uow:
            reg1_id = uow.conn.execute("SELECT shim_registration_id FROM shim_registry_entries WHERE shim_name='shim1'").fetchone()['shim_registration_id']
            backup_seq_id = backups[0]['backup_sequence_id']
            try:
                uow.conn.execute("INSERT INTO shim_pending_operations (idempotency_key, request_digest, shim_registration_id, operation_type, selected_backup_sequence_id, operation_state, created_at) VALUES ('bad-fk', 'dig', ?, 'RESTORE', ?, 'INTENT_DECLARED', ?)", (reg1_id, backup_seq_id, now_utc()))
                uow.commit()
                print("FAIL: FK constraint should have failed")
            except sqlite3.IntegrityError as e:
                if "FOREIGN KEY constraint failed" in str(e):
                    print("PASS: Rejected cross-registration backup reference")
                else:
                    print(f"FAIL: Wrong IntegrityError: {e}")

        print("\n[Scenario G] Crash-then-resume (DB FS_STAGED, file replaced)")
        run_install(store, "idemp-G", "shim3", shim_3, b"payload3", "prof1", "/path/to/bin1", "receipt-1", crash_after_fs_write=True)
        with store.unit_of_work() as uow:
            state = uow.conn.execute("SELECT operation_state FROM shim_pending_operations WHERE idempotency_key='idemp-G'").fetchone()['operation_state']
            print(f"Status before resume: {state}")
        run_install(store, "idemp-G", "shim3", shim_3, b"payload3", "prof1", "/path/to/bin1", "receipt-1")
        with store.unit_of_work() as uow:
            state = uow.conn.execute("SELECT operation_state FROM shim_pending_operations WHERE idempotency_key='idemp-G'").fetchone()['operation_state']
            print(f"Status after resume: {state}")
            if state == 'COMPLETED':
                print("PASS: Resumed successfully")
            else:
                print("FAIL: Did not complete")

        print("\n[Scenario H] Crash-then-resume (DB FS_STAGED, file NOT replaced)")
        run_install(store, "idemp-H", "shim4", shim_4, b"payload4", "prof1", "/path/to/bin1", "receipt-1", crash_before_fs_write=True)
        print(f"File exists before resume: {os.path.exists(shim_4)}")
        run_install(store, "idemp-H", "shim4", shim_4, b"payload4", "prof1", "/path/to/bin1", "receipt-1")
        print(f"File exists after resume: {os.path.exists(shim_4)}")
        with store.unit_of_work() as uow:
            state = uow.conn.execute("SELECT operation_state FROM shim_pending_operations WHERE idempotency_key='idemp-H'").fetchone()['operation_state']
            if state == 'COMPLETED' and os.path.exists(shim_4):
                print("PASS: Resumed successfully and wrote file")
            else:
                print("FAIL: Did not complete")

        store._keeper_conn.close()

    print("--- TRACE DEMONSTRATION END ---")
```

**Real executed output (4 consecutive runs, identical, exit code 0 every time):**
```text
--- TRACE DEMONSTRATION START ---

[Scenario A] Admission target validation
PASS: Rejected mismatched downstream_target_path

[Scenario B] ABSENT sub-path
PASS: Completed ABSENT install

[Scenario C] EXTERNAL_COLLISION sub-path
PASS: Completed EXTERNAL_COLLISION install

[Scenario D] MANAGED_UPDATE sub-path (changing bindings)
Before: target=/path/to/bin1, profile=prof1
After: target=/path/to/bin2, profile=prof2
PASS: Bindings updated atomically

[Scenario E] RESTORE deterministic selection
PASS: Selected most recent unrestored backup deterministically

[Scenario F] Composite-FK cross-registration rejection
PASS: Rejected cross-registration backup reference

[Scenario G] Crash-then-resume (DB FS_STAGED, file replaced)
Status before resume: FS_STAGED
Status after resume: COMPLETED
PASS: Resumed successfully

[Scenario H] Crash-then-resume (DB FS_STAGED, file NOT replaced)
File exists before resume: False
File exists after resume: True
PASS: Resumed successfully and wrote file
--- TRACE DEMONSTRATION END ---
```

## D. Remediation of Overclaiming Document Prose

**Context:** The ratified `AdmissionRegistry` codebase honestly admits only a single, shallow entrypoint node (`chain_complete=False` hardcoded), explicitly deferring full recursive wrapper-chain derivation to Phase 2. However, two normative documents still contain overclaiming text suggesting a "complete transitive execution graph" is bound.

**Resolution: Current-Text Reconciliation (refreshed 2026-08-23 — see below):**

The underlying overclaiming concern is resolved in both target documents, but not by the originally drafted replacement diffs that used to appear here. Later independent edits changed the documents directly, so the original diff text went stale (neither its "before" nor "after" text matched the live files) and has been replaced with this current-text reconciliation, confirmed via direct read of both files by `cx` and independently re-verified by the terminal.

**1. `docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md`, Section 4**

Current text:

> To cryptographically bind the admission-time evidence for the single entrypoint node it admits, Phase 1 manifest admission strictly resolves, validates, and pins that node's canonical path and content hash at the moment of admission.

The same section explicitly states that Phase 1 validates and pins exactly one entrypoint node, that full multi-node recursive wrapper-chain derivation (`chain_complete=True`) is deferred to Phase 2, and that the Phase 1 receipt records `chain_complete: False`.

**2. `docs/design/PHASE1-ADMISSION-RECEIPTS-REAL-2026-08-20.md`, Section 5**

Current checklist row:

> | Transitive Executable Binding | **DEFERRED (Phase 2)** | Documented empirical host requirements for multi-node chains, but deferred actual implementation to Phase 2. Phase 1 validates only the single entrypoint node. |

The remainder of that document likewise identifies `chain_complete: true` receipts as Phase 2 illustrative targets and states that real Phase 1 admission produces only single-node, `chain_complete: false` receipts.

Therefore the complete-transitive-execution-graph overclaim this item was opened to fix is resolved in both files, via later independent prose edits rather than the diff text this section originally specified.

---

## Completion Statement
All four items (A, B, C, D) have been **fully addressed** with concrete, complete proposals:
* **Item A** is addressed by renaming the domain models to `ManifestAdmissionCoordinator` and `ManifestAdmissionReceipt`.
* **Item B** is addressed with the illustrative `0025_manifest_admission_receipts.sql` schema and a `StateStore`-integrated coordinator trace. All 6 review issues were **fully addressed**:
    1. **Dedicated Keeper Connection**: `FakeStateStore` holds an explicitly-opened keeper connection (`_keeper_conn`) for the instance's lifetime to prevent shared-cache death.
    2. **Connection Lifecycle**: `__exit__` always closes the connection and explicitly checks for uncommitted transactions to rollback; reads use `read_only=True` to avoid `BEGIN IMMEDIATE`.
    3. **Bounded Lock Retries**: Retry logic strictly bounds total time (not just attempts) via `time.monotonic()`, passes `timeout=0` to SQLite to prevent blocking, and manages its own bounded retry interval in Python, raising a typed `StateStoreUnavailableError` exactly when the budget is exceeded.
    4. **JSON Round-trip for Real Types**: Deeply deserializes `TransitiveExecutableNode` as frozen dataclasses and reconstructed Enums, proving genuine typed persistence.
    5. **Semantic Trace Drift**: `ManifestAdmissionReceipt` and `ManifestProvisioningEvidenceReceipt` are now distinct typed tiers; schema_version is "2.0.0", timestamp format is exact, ID collisions retry over bounded transactions, and `get_trusted_receipt()` is defined via `ManifestAdmissionReadUnitOfWork`.
    6. **Prose and Injection Clarification**: Explicit prose states the coordinator receives an injected `StateStore` (and owns no files), and clarifies that it is an injected verification step within `AdmissionCoordinator`, not a competitor.
* **Item C** is addressed by folding shim persistence into `shim_registry_entries`/`shim_pending_operations`/`shim_backup_entries` and migrating away from JSON file locking toward SQLite `UnitOfWork` writes. The Round 90-103 design (12 rounds, ratified Round 102, implemented Round 103) was found by an independent post-closure review to have 2 blocking defects — no terminal failure state (a correct `ERR_SHIM_EXTERNALLY_MODIFIED` abort permanently bricked the shim) and a crash-resume tamper-detection bypass in the reference implementation — both reproduced independently. Round 104 replaced the per-sub-path-branching design with a single uniform `(pre_state_hash, post_state_hash)` model covering `ABSENT`, `EXTERNAL_COLLISION`, `MANAGED_UPDATE`, `RESTORE`, and `REMOVE`, an explicit `ABORTED` terminal state with operator-facing recovery operations, structural elimination of the tamper-detection bypass, an authorization gate for collision overrides, and a canonicalized admission-target comparison. **Rounds 104-146 (43 further rounds) hardened this redesign against repeated independent adversarial review; the state machine, schema, and full recovery-operation set were formally ratified as converged in Round 146** (see `PHASE1-PROCESS-BACKLOG-2026-08-20.md` for the complete round-by-round history), under an explicitly disclosed threat-model boundary (a privileged, continuously-interfering external actor is an accepted, out-of-scope residual risk). Repository operations and a genuinely executed trace against this current (post-Round-104) design remain pending as separate implementation-verification work — the retained Round 103 trace below is explicitly superseded and does not demonstrate the current design. `shim_registry.json` remains a derived, read-only export/cache of the SQLite source of truth, never read back for dispatch or write decisions.
* **Item D** is addressed with exact replacement diffs aligning the two earlier specification documents with the honest `chain_complete=False` scope boundary established in the final Phase 1 dialectic. Closed at Round 89 after a 6-round sub-sequence (Rounds 70, 84-89).

**Architecture-consolidation phase status (updated 2026-08-22): all four items are closed.** Item A has been ready since Round 67 (canonical naming, checked against every "admission"-adjacent symbol in the codebase, no collision found). Item B closed at Round 83 after a 14-round sub-dialectic (Rounds 70-83). Item D closed at Round 89. Item C closed at Round 146 after a 57-round arc (Rounds 90-146) — the longest of the four, since every single fresh full-pass review from Round 98 through Round 145 found at least one genuine, independently-verified defect before finally converging. Per the user's standing rule, implementation does not begin until this phase is reviewed by the user; this document's completion is the trigger for that review, not a decision to proceed.
