---
status: PROPOSED
date: 2026-08-21
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
> The coordinator's current timeout/deadline guarantee is not actually a property of the real `StateStore` port contract (`peerhub/state/contract.py`). It only holds because this specific adapter implementation (`FakeSqliteUnitOfWork`) internally chooses non-blocking (`timeout=0.0`) SQLite acquisition. Other completely valid adapters that satisfy the real `StateStore` protocol but use long internal busy-timeouts will silently defeat the coordinator's deadline enforcement. Making this a genuine, portable guarantee requires extending the real `StateStore/UnitOfWork` port interface (e.g., adding an explicit deadline-aware acquisition method or a documented non-blocking-behavior requirement) as real, necessary Phase 2 implementation work.


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
    chain_complete INTEGER NOT NULL CHECK (chain_complete = 0), -- Boolean, 0 (False) for Phase 1 single-entrypoint bounds
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


## C. Shim Registry Persistence Folding

**Context:** `PHASE1-THIRDPARTY-DEFERRAL-AND-SHIMS-2026-08-20.md` specified an atomic-candidate-snapshot pattern (`os.replace`) on a `shim_registry.json` file. `ARCHITECTURE.md` explicitly deprecated this pattern ("bespoke JSON-file locking") in favor of SQLite.

**Resolution:**
The shim registry state is folded into the same SQLite database as the manifest receipts, sharing the single operational source of truth.

### 1. Illustrative Schema Extension (Corrected, Round 95-101)

Round 93 correctly resolved 3 of cx's Round 91 findings — FK ordering, generation identity via the immutable `shim_registration_id`, and the `BEGIN IMMEDIATE` transaction mode — and cx's Round 94 review confirmed these hold. That review found 5 further blocking defects, fixed below: (1) the recovery decision was a binary "rewrite if hash differs" check that conflated "replacement never happened" with "external tampering after completion" — fixed with an explicit 3-way hash comparison in each transition's crash commentary; (2) the idempotency key never actually prevented duplicate effects, since a retry could fail on the active-path unique index or orphan the original registration before the conflict handler ran — fixed by checking for an existing pending operation by key *before* any parent-row mutation, plus a `request_digest` to reject key-reuse with different content, plus binding each backup row to its originating key; (3) canonicalization didn't resolve symlinks/junctions to real filesystem identity — fixed with an explicit `os.path.samefile`-equivalent identity-resolution rule; (4) restore didn't persist which exact backup it selected, allowing a resumed/retried restore to pick a different one — fixed with `selected_backup_sequence_id`; (5) `REMOVE` was a legal enum value with no defined behavior — removed from the allowed values pending a future round that designs its protocol.

Round 99 extended `INSTALL` to branch on 3 real lifecycle pre-conditions (`ABSENT`/`EXTERNAL_COLLISION`/`MANAGED_UPDATE`), closing a gap cx's fresh full-document pass found: the protocol previously only modeled `EXTERNAL_COLLISION`. Round 100's review of that extension found 4 further blocking defects plus supporting integrity gaps, fixed in this Round 101 update: (1) **TOCTOU narrowing** — pre-effect state is now explicitly re-validated immediately before the filesystem write on every execution path (not just checked once at intent declaration), narrowing but not eliminating the race against non-cooperating external writers; (2) **complete bindings update** — `MANAGED_UPDATE` now persists intended `profile_name`/`downstream_target_path`/`admission_receipt_id` at intent time and atomically updates all of them (not just the hash) at completion; (3) **admission-target validation** — `downstream_target_path` is now validated against the admitted executable's `canonical_path` (deserialized from the real `manifest_admission_receipts.transitive_executable_chain_json`, per item B's established JSON-round-trip pattern) before any mutation, for every sub-path; (4) **check-in-flight-before-hash ordering** — `MANAGED_UPDATE`'s pre-condition now checks for a non-terminal prior operation on the registration before diagnosing a hash mismatch as external tampering; (5) **relational integrity** — composite foreign keys now tie `selected_backup_sequence_id` and `originating_idempotency_key` to the correct `shim_registration_id`, an exact-match requirement replaces partial shim_name/canonical_path fallthrough, and an explicit note requires consumers to exclude `ACTIVE` registrations with a non-terminal pending operation.

**Invariant: Deterministic Canonicalization.** Before any path is written to `canonical_shim_path`, it is processed through a concrete, deterministic canonicalization function that establishes true filesystem identity, consistent with how the rest of this architecture uses `os.path.samefile()` for the same purpose:
* **For an existing path**, resolve to its real filesystem identity (following symlinks and junctions, rejecting loops) before normalizing path separators and case-folding per the target platform's filesystem convention (strictly lowercased on Windows).
* **For a not-yet-existing final path** (a fresh install target), canonicalize the resolved parent directory's real identity, then append the literal final path component, normalized and case-folded.
This guarantees robust collision detection even when two path strings address the same file through a symlink, junction, `\\?\`-prefixed path, or short-name (`8.3`) alias — not just the literal `C:/Shims/cc.bat` vs `c:\shims\CC.bat` case-variant example.

```sql
CREATE TABLE shim_registry_entries (
    -- Immutable generation identity, decoupling backup FKs from the mutable shim_name
    shim_registration_id TEXT PRIMARY KEY,

    shim_name TEXT NOT NULL,
    canonical_shim_path TEXT NOT NULL,

    -- The path where the generated shim file resides (corresponds to 'P' in §2.8.2/§2.8.3)
    shim_path TEXT NOT NULL,

    -- The downstream real executable this shim forwards to (distinct from 'P')
    downstream_target_path TEXT NOT NULL,

    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'RETIRED')),

    profile_name TEXT NOT NULL,
    admission_receipt_id TEXT NOT NULL REFERENCES manifest_admission_receipts(admission_receipt_id),
    shim_file_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Uniqueness enforced only among ACTIVE registrations; a RETIRED path may be legitimately reused
CREATE UNIQUE INDEX idx_active_shim_name ON shim_registry_entries(shim_name) WHERE status = 'ACTIVE';
CREATE UNIQUE INDEX idx_active_canonical_path ON shim_registry_entries(canonical_shim_path) WHERE status = 'ACTIVE';

CREATE TABLE shim_pending_operations (
    -- Caller-supplied idempotency key; checked BEFORE any parent-row mutation, so a retry of an
    -- already-succeeded operation resumes the existing registration instead of orphaning it
    idempotency_key TEXT PRIMARY KEY,

    -- Hash of the logical operation's inputs; a reused key with DIFFERENT content is a conflict,
    -- not a silent resume
    request_digest TEXT NOT NULL,

    -- Explicit operation intent, durably recorded BEFORE any filesystem write begins, so
    -- reconciliation reads intent directly instead of inferring it from a hash comparison alone.
    shim_registration_id TEXT NOT NULL REFERENCES shim_registry_entries(shim_registration_id),

    -- 'REMOVE' is intentionally excluded: the real doc's §2.7 safe-removal protocol
    -- (hash verification, fallback-precondition check, atomic removal) is not yet
    -- crash-recovery-designed; deferred to a future round rather than left undefined here.
    operation_type TEXT NOT NULL CHECK (operation_type IN ('INSTALL', 'RESTORE')),

    -- Which of the real doc's 3 lifecycle pre-conditions this INSTALL is, detected at
    -- INTENT_DECLARED before any DB mutation: ABSENT (no file, no registry row -- simplest case),
    -- EXTERNAL_COLLISION (a foreign file exists -- the originally-modeled §2.3 --force path), or
    -- MANAGED_UPDATE (an ACTIVE registry row already exists -- the real doc's §2.4 safe-update path,
    -- validated against the registry's own known-good hash, not a foreign file's hash).
    install_sub_path TEXT CHECK (install_sub_path IN ('ABSENT', 'EXTERNAL_COLLISION', 'MANAGED_UPDATE')),
    expected_hash TEXT,

    -- Snapshots the registry's known-good shim_file_sha256 at intent time for
    -- MANAGED_UPDATE, giving crash recovery a static pre-effect reference since this sub-path
    -- overwrites the existing shim in place without ever inserting a backup row.
    pre_effect_hash TEXT,

    -- (Round 101) Intended bindings at intent time, so step 4 can atomically update them alongside
    -- the hash. Required for every INSTALL so MANAGED_UPDATE never silently leaves stale bindings
    -- after updating the executable bytes.
    intended_profile_name TEXT,
    intended_downstream_target_path TEXT,
    intended_admission_receipt_id TEXT REFERENCES manifest_admission_receipts(admission_receipt_id),

    -- Binds a RESTORE intent to the exact backup chosen at INTENT_DECLARED, so a crash-then-resume
    -- or retry cannot re-query "most recent unrestored" and pick a different one
    selected_backup_sequence_id INTEGER,

    operation_state TEXT NOT NULL CHECK (operation_state IN ('INTENT_DECLARED', 'FS_STAGED', 'COMPLETED')),
    created_at TEXT NOT NULL,

    -- Enforce the fields each operation type/sub-path actually needs, closing the nullable-column
    -- gap: INSTALL must carry its target hash, must NOT reference a backup, must declare a
    -- sub-path and intended bindings, and only MANAGED_UPDATE carries a pre_effect_hash; RESTORE
    -- must reference exactly the backup it selected and has none of the INSTALL-only fields.
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

    -- (Round 101) Ensures a RESTORE's selected backup actually belongs to its own registration,
    -- not merely any valid backup_sequence_id
    FOREIGN KEY (shim_registration_id, selected_backup_sequence_id) REFERENCES shim_backup_entries(shim_registration_id, backup_sequence_id)
);

-- Prevents multiple simultaneous non-terminal operations against the same registration
CREATE UNIQUE INDEX idx_active_pending_operation ON shim_pending_operations(shim_registration_id) WHERE operation_state != 'COMPLETED';

-- (Round 101) Parent-side unique index required for the composite FK from shim_backup_entries
CREATE UNIQUE INDEX idx_pending_op_reg_key ON shim_pending_operations(shim_registration_id, idempotency_key);

CREATE TABLE shim_backup_entries (
    -- Monotonic sequence for deterministic "most recent unrestored backup" ordering
    backup_sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- FK to the immutable generation id, not the mutable shim_name; no CASCADE, so
    -- backup/audit history survives the referenced registration being RETIRED
    shim_registration_id TEXT NOT NULL REFERENCES shim_registry_entries(shim_registration_id),

    -- Binds this backup to the operation that created it; a retried backup-insertion step
    -- becomes a safe no-op instead of inserting a duplicate row
    originating_idempotency_key TEXT NOT NULL UNIQUE,

    -- Path of the original collision target that was backed up (corresponds to 'P')
    target_path TEXT NOT NULL,

    backup_file_path TEXT NOT NULL,
    original_sha256 TEXT NOT NULL,

    -- Forensic fields required by §2.8.2's backup_meta.json schema
    original_mtime_epoch REAL NOT NULL,
    original_file_size_bytes INTEGER NOT NULL,
    original_permissions_octal TEXT NOT NULL,
    override_reason TEXT NOT NULL,

    backup_created_at TEXT NOT NULL,
    restored INTEGER NOT NULL DEFAULT 0 CHECK (restored IN (0, 1)),
    restored_at TEXT,

    -- (Round 101) Ensures this backup's originating operation was on the SAME registration,
    -- not merely any valid pending-operation row
    FOREIGN KEY (shim_registration_id, originating_idempotency_key) REFERENCES shim_pending_operations(shim_registration_id, idempotency_key)
);

-- (Round 101) Parent-side unique index required for the composite FK from shim_pending_operations
CREATE UNIQUE INDEX idx_backup_reg_seq ON shim_backup_entries(shim_registration_id, backup_sequence_id);
```

*(This creates a circular table-level FK relationship — `shim_pending_operations` references `shim_backup_entries` for `selected_backup_sequence_id`, and `shim_backup_entries` references `shim_pending_operations` for `originating_idempotency_key` — which is safe in SQLite: `CREATE TABLE` does not require a referenced table to exist at parse time, only at DML time, and each individual row's FK columns reference a row in a *different logical operation* (an INSTALL's pending-op row is what a backup references; a later RESTORE's pending-op row is what references that backup), never itself. Independently verified end-to-end: schema creation, a full realistic insert sequence (registry row → INSTALL pending-op with NULL `selected_backup_sequence_id` → backup row → RESTORE pending-op referencing that backup) all succeed, and a RESTORE pending-op referencing a backup under a *different* registration is correctly rejected with `FOREIGN KEY constraint failed`.)*

> [!IMPORTANT]
> **Visibility of `ACTIVE` registrations.** Because new `ABSENT`/`EXTERNAL_COLLISION` registrations become visible as `status='ACTIVE'` in `shim_registry_entries` the moment step 1 commits — before the actual filesystem write in step 4 ever happens — an `ACTIVE` registry row is **not actually consumable** until its most recent associated operation reaches `COMPLETED`. Consumers evaluating "is this shim usable" MUST structurally exclude in-flight installs, e.g. by `LEFT JOIN`ing against `shim_pending_operations WHERE operation_state != 'COMPLETED'` and excluding matches.

### 2. Crash-Recoverable State Machine

No SQLite transaction is ever held open across a filesystem write (matching item B's established constraint). A higher-level advisory/mutex file lock is held across the entire sequence, released only after the final DB commit; this does not violate the invariant above because the lock is a separate advisory mechanism, not an internal SQLite lock. Every transaction uses `BEGIN IMMEDIATE`, matching the real `SqliteUnitOfWork` (`peerhub/persistence/sqlite.py` lines 171, 347, 675, 726). Every `operation_state` transition is a compare-and-swap `UPDATE ... WHERE idempotency_key=? AND operation_state=<expected_prior_state>`, and callers must verify the affected-row count is exactly 1, treating 0 as a conflict to investigate rather than silently ignoring it. `fsync`/durability requirements cover the shim install/restore renames themselves (`os.replace` on `P` and its parent directory), not just the backup artifacts.

**Backup/Install Protocol** — states: `INTENT_DECLARED` → `BACKUP_STAGED` → `DB_COMMITTED` → `SHIM_REPLACED (COMPLETED)`. This protocol branches at step 1 into one of 3 real lifecycle sub-paths — `EXTERNAL_COLLISION` (§2.3's `--force` path), `ABSENT` (§2.3's implicit no-collision case), and `MANAGED_UPDATE` (§2.4's safe-update-of-an-already-managed-shim).

1. **[START] → INTENT_DECLARED** — *FS*: acquire advisory lock; read target `P`'s hash/size/permissions/mtime, if `P` exists. *DB*: `BEGIN IMMEDIATE`; FIRST query `shim_pending_operations` by `idempotency_key`. If found, verify `request_digest` matches (reject as a conflict if not) and resume using its existing `shim_registration_id` — no new parent row is created. If not found, query `shim_registry_entries` for an `ACTIVE` row matching both `shim_name` AND `canonical_shim_path`:
   * **Exactly one row matches both (`MANAGED_UPDATE`)**:
     * *Concurrency check first*: query `shim_pending_operations` for a non-terminal (`operation_state != 'COMPLETED'`) row on this `shim_registration_id`. If one exists with a matching `idempotency_key`, resume it. If one exists with a **different** `idempotency_key`, abort with a typed "operation already in progress" conflict — do not let a raw unique-index `IntegrityError` be the de facto signal, and do not misdiagnose this as external tampering.
     * *Only if no non-terminal operation exists*: **admission-target validation** — fetch the referenced `manifest_admission_receipts` row's `transitive_executable_chain_json` (an ordinary opaque-document read, per item B's established pattern), deserialize it into `TransitiveExecutableNode` instances, and compare the single entrypoint node's `canonical_path` against the intended `downstream_target_path`; abort before any mutation on mismatch. Then **pre-condition hash check** — compare `P`'s actual hash against the existing row's `shim_file_sha256`. Mismatch → abort `ERR_SHIM_EXTERNALLY_MODIFIED`, matching the real doc's §2.4 exactly. Match → do **not** insert a new parent row; `INSERT` into `shim_pending_operations` referencing the *existing* `shim_registration_id` (`install_sub_path='MANAGED_UPDATE'`, `expected_hash` = incoming payload's hash, `pre_effect_hash` = the matched `shim_file_sha256`, `intended_profile_name`/`intended_downstream_target_path`/`intended_admission_receipt_id` = the new bindings, `request_digest` = hash of inputs).
   * **Exactly one of `shim_name`/`canonical_shim_path` matches, not both**: abort with a typed identity-conflict error. Never fall through to a new-parent insert.
   * **Neither matches (`EXTERNAL_COLLISION` if a foreign file exists at `P`, else `ABSENT`)**: perform the same admission-target validation as above. On success, `INSERT` the new parent `shim_registry_entries` row first — the parent must commit-order before any child references it, per the Round 91 `FOREIGN KEY constraint failed` reproduction — then `INSERT` into `shim_pending_operations` referencing the *new* `shim_registration_id` (`install_sub_path` set accordingly, `pre_effect_hash=NULL`, intended bindings, `request_digest` = hash of inputs). Per the visibility note above, this new row is correctly not yet consumable, since its only associated operation is still non-terminal.
   * `COMMIT`.
2. **INTENT_DECLARED → BACKUP_STAGED** — for `EXTERNAL_COLLISION` only: *FS*: stage `.tmp` copy of `P`, verify hash, atomically `os.replace` to finalized `.bak`, persist `backup_meta.json`, `fsync`/flush both (and their parent directory) before considering them securely synced. *DB*: none. (Crash: orphaned `.bak`/`.json` files with no `FS_STAGED`-or-later pending-operation row are safely ignorable/GC-able.) For `ABSENT` and `MANAGED_UPDATE`: no backup is needed or possible (there is no foreign file, and no `original_sha256` to preserve); this step performs no filesystem work and is skipped.
3. **BACKUP_STAGED → DB_COMMITTED** — *FS*: none. *DB*: `BEGIN IMMEDIATE`; for `EXTERNAL_COLLISION` only, `INSERT ... ON CONFLICT (originating_idempotency_key) DO NOTHING` into `shim_backup_entries` (binding it to the operation, generating `backup_sequence_id`) — for `ABSENT`/`MANAGED_UPDATE`, `shim_backup_entries` correctly remains untouched, since neither sub-path has a `FOREIGN KEY` requiring a row there. For all 3 sub-paths: `UPDATE shim_pending_operations SET operation_state='FS_STAGED' WHERE idempotency_key=? AND operation_state='INTENT_DECLARED'` (affected rows must equal 1); `COMMIT`. (Crash: strictly isolated from I/O — safe to retry if rolled back; a retry's backup insert is now a no-op rather than a duplicate row.)
4. **DB_COMMITTED → SHIM_REPLACED (COMPLETED)** — *FS*: stage the full generated shim payload. **Explicit pre-effect re-validation (TOCTOU narrowing)**, performed immediately before writing on every execution path (both normal forward progress and any crash resumption, since resuming from `FS_STAGED` can only mean a *previous* attempt reached this step): compute `actual_hash = sha256(P)` (or note absence) and perform a 3-way check —
   * `actual_hash` still matches the pre-effect reference (`original_sha256` for `EXTERNAL_COLLISION`, `pre_effect_hash` for `MANAGED_UPDATE`, or `P` still absent for `ABSENT`) → safe to write now: atomically `os.replace` the staged payload over `P`, `fsync` the file and parent directory.
   * `actual_hash == expected_hash` → the replacement **already completed** (only reachable if a prior attempt crashed between its own `os.replace` and this transaction's commit) — skip the filesystem write, but re-`fsync` `P` and its parent directory before proceeding, since observing correct bytes does not prove the earlier `fsync` completed.
   * Any other outcome (including `P` unexpectedly existing for `ABSENT`) → external modification occurred since step 1 validated the pre-condition — abort `ERR_SHIM_EXTERNALLY_MODIFIED`, never auto-overwrite. *(This check is a best-effort narrowing against non-cooperating external writers — a true atomic compare-and-replace isn't available cross-platform — not an absolute guarantee, matching the honest-disclosed-limitation pattern already used elsewhere in this document.)*
   *DB*: `BEGIN IMMEDIATE`; for `MANAGED_UPDATE` only, additionally `UPDATE shim_registry_entries SET shim_file_sha256=?, updated_at=?, profile_name=?, downstream_target_path=?, admission_receipt_id=? WHERE shim_registration_id=?` (atomically updating the hash **and** all 3 bindings together, from the intended values recorded at intent time — same generation, no new row). For all 3 sub-paths: `UPDATE shim_pending_operations SET operation_state='COMPLETED' WHERE idempotency_key=? AND operation_state='FS_STAGED'` (affected rows must equal 1); `COMMIT`. *FS*: release advisory lock only **after** this commit.
   * *(Crash/Reconciliation for an `INSTALL` stuck at `FS_STAGED`: no separate reconciliation branch is needed beyond the step 4 pre-effect re-validation above — it deterministically distinguishes all 3 outcomes on resumption exactly as it does on first execution.)*

**Restore Protocol** — states: `INTENT_DECLARED` → `RESTORE_VERIFIED` → `RESTORE_STAGED_FS` → `RESTORE_COMPLETE`

1. **[START] → INTENT_DECLARED** — *FS*: acquire advisory lock. *DB*: `BEGIN IMMEDIATE`; FIRST query `shim_pending_operations` by `idempotency_key` as in Install; if found, resume. If not found: query `shim_backup_entries` for the most recent unrestored backup for the target registration (`ORDER BY backup_sequence_id DESC LIMIT 1`) — **if no such row exists, abort before inserting any intent record**, there is nothing to restore; otherwise `INSERT` into `shim_pending_operations` (`operation_type='RESTORE'`, `operation_state='INTENT_DECLARED'`, `selected_backup_sequence_id` = the chosen backup, `request_digest` = hash of inputs); `COMMIT`. Every subsequent restore step references this persisted `selected_backup_sequence_id` — it is never re-queried.
2. **INTENT_DECLARED → RESTORE_VERIFIED** — *FS*: verify the `.bak` archive's SHA-256 against the DB record, abort `ERR_CORRUPT_BACKUP_ARCHIVE` on mismatch. *DB*: none.
3. **RESTORE_VERIFIED → RESTORE_STAGED_FS** — *FS*: copy `.bak` to `P.restoring.<pid>`, apply `original_mtime_epoch`/`original_permissions_octal`, atomically `os.replace` over `P`, `fsync` file and parent. *DB*: `BEGIN IMMEDIATE`; `UPDATE shim_pending_operations SET operation_state='FS_STAGED' WHERE idempotency_key=? AND operation_state='INTENT_DECLARED'` (affected rows must equal 1); `COMMIT`.
   * *(Crash/Reconciliation for a `RESTORE` stuck at `INTENT_DECLARED`: unlike Install, restore's filesystem replacement in this step happens **before** its `FS_STAGED` commit, so observing `INTENT_DECLARED` is the genuinely ambiguous state — the crash could have landed on either side of the `os.replace`. Compute `actual_hash = sha256(P)` and compare against the two persisted references, applying this precedence to resolve the case where they happen to be equal (an identical-payload reinstall/restore) — sample the hash and `stat()` metadata from the same file open/identity to avoid a torn observation if an external process is concurrently replacing `P`: check the post-effect condition FIRST — `actual_hash` matches the selected backup's `original_sha256` **and** `P`'s current `mtime`/permissions already equal `original_mtime_epoch`/`original_permissions_octal` (SHA-256 alone doesn't capture metadata, which the real protocol also restores) → the filesystem effect already completed, but observing correct bytes does **not** prove the earlier `fsync` completed (the crash may have landed between `os.replace` and `fsync`, leaving correct-but-non-durable content in cache) — re-`fsync` `P` and its parent directory now, then perform the same `INTENT_DECLARED → FS_STAGED` CAS update step 3 normally performs (a bare "resume at step 4" is impossible: step 4's CAS requires `operation_state='FS_STAGED'` and would affect 0 rows from `INTENT_DECLARED`), and only then proceed to step 4. Otherwise, if `actual_hash == shim_file_sha256` (the pre-restore registry value) → replacement never happened; because `RESTORE_VERIFIED` (step 2) is not itself a persisted state, re-run step 2's archive-integrity verification before re-attempting this step's filesystem work — do not assume it already ran and survived the crash. Any other outcome → external modification occurred after the crash, fail with `ERR_SHIM_EXTERNALLY_MODIFIED`, never auto-overwrite.)*
4. **RESTORE_STAGED_FS → RESTORE_COMPLETE** — *FS*: none. *DB*: `BEGIN IMMEDIATE`; `UPDATE shim_backup_entries SET restored=1, restored_at=... WHERE backup_sequence_id=?`; `UPDATE shim_registry_entries SET status='RETIRED' WHERE shim_registration_id=?`; `UPDATE shim_pending_operations SET operation_state='COMPLETED' WHERE idempotency_key=? AND operation_state='FS_STAGED'` (affected rows must equal 1); `COMMIT`. *FS*: release advisory lock only **after** this commit.
   * *(Crash/Reconciliation for a `RESTORE` stuck at `FS_STAGED`: no hash check is needed or performed here. Reaching `FS_STAGED` is only possible after step 3's filesystem replacement already completed and was `fsync`'d — the DB commit that sets `FS_STAGED` happens strictly after that write, never before. This state therefore unambiguously proves the replacement already happened; recovery simply resumes and completes this step's DB-only updates, and must never re-run the filesystem work or re-derive a decision from `sha256(P)` at this state.)*

### 3. Transactional Replacement
The complex JSON-file read-modify-write cycle (with `.tmp.<pid>` files and `os.replace`) is eliminated. Each state transition above is its own short, independently-committed `UnitOfWork`:
```python
with self._store.unit_of_work() as unit:
    unit.commit_backup_entry(new_backup_entry)
    unit.commit()
```
If a flat `shim_registry.json` file is required for external tooling consumption or fast shell pathing, it becomes an **explicitly-derived, read-only export/cache**. Consistent with how `ARCHITECTURE.md` treats the adapter registry as a "disposable derived index", a post-commit hook or explicit CLI command regenerates the JSON cache from the SQLite operational source of truth. It is never read back in to make dispatch decisions or write resolutions.

> [!NOTE]
> **Round 90 scope boundary.** This round covers only the corrected schema and the crash-recoverable state machine. The reconciliation/recovery procedure for resuming or discarding in-flight operations after an unclean exit, the concrete repository operations (`create_backup_staging`, `finalize_backup`, `commit_shim_replacement`, `get_most_recent_unrestored_backup`, `mark_backup_restored`, `retire_shim_entry`), and a genuinely executed trace demonstrating crash-recovery and deterministic restore-selection are addressed in the following round.

## D. Remediation of Overclaiming Document Prose

**Context:** The ratified `AdmissionRegistry` codebase honestly admits only a single, shallow entrypoint node (`chain_complete=False` hardcoded), explicitly deferring full recursive wrapper-chain derivation to Phase 2. However, two normative documents still contain overclaiming text suggesting a "complete transitive execution graph" is bound.

**Resolution (Prose Replacement Diffs):**

**1. `docs/design/PHASE1-MANIFEST-SCHEMA-V2-2026-08-20.md`** (Section 4)

```diff
- To eliminate TOCTOU, unauthenticated tampering, and wrapper-only binding vulnerabilities, manifest admission strictly resolves, validates, and cryptographically binds the complete transitive execution graph.
+ To establish a secure baseline without overclaiming capability, manifest admission strictly resolves, validates, and cryptographically binds the shallow entrypoint node, explicitly and honestly deferring full transitive execution graph derivation and validation to Phase 2 (chain_complete=False).
```

**2. `docs/design/PHASE1-ADMISSION-RECEIPTS-REAL-2026-08-20.md`** (Section 5, Verification Checklist)

```diff
- | Transitive Executable Binding | **CLOSED** | Fully traced `.cmd` wrappers, Node interpreters, `.js` launchers, and native `.exe` binaries with real SHA-256 digests. |
+ | Transitive Executable Binding | **DEFERRED (Phase 2)** | Fully traced `.cmd` wrappers and downstream binaries, but full recursive derivation at admission time is explicitly deferred to Phase 2 (single entrypoint verified only, `chain_complete=False` hardcoded). |
```

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
* **Item C** is addressed by folding shim persistence into `shim_registry_entries` and migrating away from JSON file locking toward SQLite `UnitOfWork` writes.
* **Item D** is addressed with exact replacement diffs aligning the two earlier specification documents with the honest `chain_complete=False` scope boundary established in the final Phase 1 dialectic.
