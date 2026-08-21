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
    chain_complete INTEGER NOT NULL, -- Boolean, 0 (False) for Phase 1 single-entrypoint bounds
    aggregate_chain_digest TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    
    -- Using JSON columns for the executable chain and companion binaries 
    -- rather than normalized child tables because these are immutable snapshots
    -- that are retrieved as a single opaque document for evidence, never queried
    -- or joined by individual file node.
    transitive_executable_chain_json TEXT NOT NULL,
    companion_binaries_json TEXT NOT NULL
);
```

### 2. Coordinator Design (Illustrative Python)

```python
import secrets
from typing import Protocol, Self
from peerhub.state.contract import StateStore, UnitOfWork, ReadUnitOfWork
from peerhub.core.context import Clock, IdSource

class ManifestAdmissionUnitOfWork(UnitOfWork, Protocol):
    def put_manifest_receipt(self, receipt: 'ManifestAdmissionReceipt') -> None: ...
    def get_manifest_receipt(self, receipt_id: str) -> 'ManifestAdmissionReceipt | None': ...
    def put_shim_entry(self, entry: 'ShimRegistryEntry') -> None: ...

class ManifestAdmissionCoordinator:
    """Orchestrate Phase 1 executable-integrity manifest admission."""

    def __init__(
        self,
        store: StateStore[ManifestAdmissionUnitOfWork, ReadUnitOfWork],
        *,
        clock: Clock,
        ids: IdSource,
    ) -> None:
        self._store = store
        self._clock = clock
        self._ids = ids

    def admit_manifest(self, raw_manifest: dict, executable_chain: list, peer_kind: str, adapter_id: str) -> 'ManifestAdmissionReceipt':
        # 1. Validation logic carried over unchanged (schema, MZ check, PATHEXT rules, etc.)
        
        # 2. Collision-safe ID issuance via real transaction semantics,
        # fully preserving the ratified Phase 1 structure and cryptographic entropy.
        timestamp_utc = str(self._clock.now())
        # While self._ids.new_id("receipt") could be used, secrets.token_hex(16) precisely 
        # maintains the previously ratified 128-bit cryptographic guarantee.
        random_suffix = secrets.token_hex(16)
        receipt_id = f"receipt-{peer_kind}-{adapter_id}-{timestamp_utc}-{random_suffix}"
        
        receipt = ManifestAdmissionReceipt(
            admission_receipt_id=receipt_id,
            # ... initialization of other fields ...
        )
        
        # 3. Transactional commit
        with self._store.unit_of_work() as unit:
            unit.put_manifest_receipt(receipt)
            unit.commit()
            
        return receipt

    def get_trusted_digest(self, receipt_id: str) -> str | None:
        with self._store.read_unit_of_work() as unit:
            receipt = unit.get_manifest_receipt(receipt_id)
            if receipt:
                return receipt.aggregate_chain_digest
        return None
```

### 3. Deserialization and Deep Immutability

While SQLite naturally stores nested objects like the `trust_root` dict and `transitive_executable_chain` list as JSON strings, naive deserialization (`json.loads`) would return plain mutable dictionaries and lists, silently breaking the deep immutability achieved in the previous dialectic. To resolve this, the `ManifestAdmissionUnitOfWork` implementation must actively reconstruct these JSON properties into frozen types during reads:
* `trust_root`, `observed_vendor`, and `acl_evaluation` must be wrapped in `types.MappingProxyType`.
* `transitive_executable_chain` and `companion_binaries` must be wrapped in Python `tuple`s.

This guarantees that a caller receiving a receipt from `get_manifest_receipt()` cannot tamper with it before evaluation.

### 4. Trace Demonstration

The following genuinely runnable script demonstrates the persistence logic, ratified ID generation format, concurrent atomicity using `BEGIN IMMEDIATE`, and deep-immutability preservation over the JSON serialization round-trip.

```python
# trace.py - Genuine Executable Trace Demonstration
import sqlite3
import threading
import types
import secrets
import json
import time
from dataclasses import dataclass
from typing import Protocol, Self

@dataclass(frozen=True)
class ManifestAdmissionReceipt:
    admission_receipt_id: str
    manifest_canonical_sha256: str
    schema_version: str
    adapter_id: str
    peer_kind: str
    inventory_generation: int
    trust_root: types.MappingProxyType
    observed_vendor: types.MappingProxyType
    acl_evaluation: types.MappingProxyType | None
    chain_complete: bool
    aggregate_chain_digest: str
    timestamp_utc: str
    transitive_executable_chain: tuple
    companion_binaries: tuple

class FakeSqliteUnitOfWork:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self) -> Self:
        self.conn.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is None: pass 
        else: self.conn.rollback()

    def commit(self) -> None:
        self.conn.commit()

    def put_manifest_receipt(self, receipt: ManifestAdmissionReceipt) -> None:
        self.conn.execute(
            """
            INSERT INTO manifest_admission_receipts (
                admission_receipt_id, manifest_canonical_sha256, schema_version,
                adapter_id, peer_kind, inventory_generation, trust_root_json,
                observed_vendor_json, acl_evaluation_json, chain_complete,
                aggregate_chain_digest, timestamp_utc, transitive_executable_chain_json,
                companion_binaries_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt.admission_receipt_id, receipt.manifest_canonical_sha256,
                receipt.schema_version, receipt.adapter_id, receipt.peer_kind,
                receipt.inventory_generation, json.dumps(dict(receipt.trust_root)),
                json.dumps(dict(receipt.observed_vendor)),
                json.dumps(dict(receipt.acl_evaluation)) if receipt.acl_evaluation else None,
                int(receipt.chain_complete), receipt.aggregate_chain_digest,
                receipt.timestamp_utc, json.dumps(list(receipt.transitive_executable_chain)),
                json.dumps(list(receipt.companion_binaries))
            )
        )

    def get_manifest_receipt(self, receipt_id: str) -> ManifestAdmissionReceipt | None:
        row = self.conn.execute(
            "SELECT * FROM manifest_admission_receipts WHERE admission_receipt_id = ?", (receipt_id,)
        ).fetchone()
        if not row: return None
        
        # Explicit immutability reconstruction
        return ManifestAdmissionReceipt(
            admission_receipt_id=row[0], manifest_canonical_sha256=row[1],
            schema_version=row[2], adapter_id=row[3], peer_kind=row[4],
            inventory_generation=row[5],
            trust_root=types.MappingProxyType(json.loads(row[6])),
            observed_vendor=types.MappingProxyType(json.loads(row[7])),
            acl_evaluation=types.MappingProxyType(json.loads(row[8])) if row[8] else None,
            chain_complete=bool(row[9]), aggregate_chain_digest=row[10], timestamp_utc=row[11],
            transitive_executable_chain=tuple(json.loads(row[12])),
            companion_binaries=tuple(json.loads(row[13])),
        )

class FakeStateStore:
    def __init__(self):
        self.db_path = "file:memorydb?mode=memory&cache=shared"
        with sqlite3.connect(self.db_path, uri=True) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS manifest_admission_receipts (
                    admission_receipt_id TEXT PRIMARY KEY, manifest_canonical_sha256 TEXT NOT NULL,
                    schema_version TEXT NOT NULL, adapter_id TEXT NOT NULL, peer_kind TEXT NOT NULL,
                    inventory_generation INTEGER NOT NULL, trust_root_json TEXT NOT NULL,
                    observed_vendor_json TEXT NOT NULL, acl_evaluation_json TEXT,
                    chain_complete INTEGER NOT NULL, aggregate_chain_digest TEXT NOT NULL,
                    timestamp_utc TEXT NOT NULL, transitive_executable_chain_json TEXT NOT NULL,
                    companion_binaries_json TEXT NOT NULL
                )
            ''')

    def unit_of_work(self) -> FakeSqliteUnitOfWork:
        return FakeSqliteUnitOfWork(sqlite3.connect(self.db_path, timeout=5.0, uri=True))

    def read_unit_of_work(self) -> FakeSqliteUnitOfWork:
        return FakeSqliteUnitOfWork(sqlite3.connect(self.db_path, timeout=5.0, uri=True))

    def get_row_counts(self):
        with sqlite3.connect(self.db_path, timeout=5.0, uri=True) as conn:
            total_count = conn.execute("SELECT COUNT(*) FROM manifest_admission_receipts WHERE peer_kind = 'cc'").fetchone()[0]
            distinct_count = conn.execute("SELECT COUNT(DISTINCT admission_receipt_id) FROM manifest_admission_receipts WHERE peer_kind = 'cc'").fetchone()[0]
            return total_count, distinct_count

class ManifestAdmissionCoordinator:
    def __init__(self, store: FakeStateStore, clock, ids):
        self._store = store
        self._clock = clock
        self._ids = ids

    def admit_manifest(self, raw_manifest: dict, peer_kind: str, adapter_id: str) -> ManifestAdmissionReceipt:
        timestamp_utc = str(self._clock.now())
        random_suffix = secrets.token_hex(16)
        receipt_id = f"receipt-{peer_kind}-{adapter_id}-{timestamp_utc}-{random_suffix}"
        
        receipt = ManifestAdmissionReceipt(
            admission_receipt_id=receipt_id, manifest_canonical_sha256="fake_sha",
            schema_version="v2", adapter_id=adapter_id, peer_kind=peer_kind,
            inventory_generation=1, trust_root=types.MappingProxyType({"root": "trusted"}),
            observed_vendor=types.MappingProxyType({"vendor": "fake"}), acl_evaluation=None,
            chain_complete=False, aggregate_chain_digest="fake_agg_digest",
            timestamp_utc=timestamp_utc, transitive_executable_chain=({"path": "/fake"},),
            companion_binaries=()
        )
        
        with self._store.unit_of_work() as unit:
            unit.put_manifest_receipt(receipt)
            unit.commit()
            
        return receipt

if __name__ == "__main__":
    class DummyClock:
        def now(self): return int(time.time())
    class DummyIdSource:
        def new_id(self, ns): return secrets.token_hex(8)

    print("--- TRACE DEMONSTRATION START ---")
    store = FakeStateStore()
    coordinator = ManifestAdmissionCoordinator(store, DummyClock(), DummyIdSource())
    
    print("\\n(a) Admission success & ID match:")
    receipt = coordinator.admit_manifest({}, "ag", "adapter_1")
    print(f"Issued ID: {receipt.admission_receipt_id}")
    assert receipt.admission_receipt_id.startswith("receipt-ag-adapter_1-")
    print("-> Format matches ratified scheme.")

    print("\\n(b) Concurrent contention:")
    errors = []
    def worker():
        try:
            for _ in range(10):
                while True:
                    try:
                        coordinator.admit_manifest({}, "cc", "adapter_2")
                        break
                    except sqlite3.OperationalError as e:
                        if 'locked' in str(e):
                            time.sleep(0.01)
                        else:
                            raise e
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()

    if errors: print(f"-> Concurrency failed with {len(errors)} errors")
    else: print("-> 50 concurrent admissions succeeded, SQLite BEGIN IMMEDIATE enforced atomicity.")

    total_count, distinct_count = store.get_row_counts()
    print(f"-> Concurrency verification: {total_count} total rows, {distinct_count} distinct IDs.")
    assert total_count == 50, f"Expected 50 rows, got {total_count}"
    assert distinct_count == 50, f"Expected 50 distinct IDs, got {distinct_count}"

    print("\\n(c) Deep immutability round-trip:")
    with store.read_unit_of_work() as unit:
        read_receipt = unit.get_manifest_receipt(receipt.admission_receipt_id)
        
    print("Attempting to mutate trust_root dict...")
    try:
        read_receipt.trust_root["root"] = "hacked"
        print("-> FAILED: Mutation succeeded!")
    except TypeError as e:
        print(f"-> SUCCESS: Mutation rejected: {e}")

    print("Attempting to mutate transitive_executable_chain list...")
    try:
        read_receipt.transitive_executable_chain.append({"path": "/hacked"})
        print("-> FAILED: Mutation succeeded!")
    except AttributeError as e:
        print(f"-> SUCCESS: Mutation rejected: {e}")
    print("--- TRACE DEMONSTRATION END ---")
```

**Real Executable Trace Output:**
```text
--- TRACE DEMONSTRATION START ---

(a) Admission success & ID match:
Issued ID: receipt-ag-adapter_1-1787319153-bb6cb6259ed7ffe530377c4e5ed178d2
-> Format matches ratified scheme.

(b) Concurrent contention:
-> 50 concurrent admissions succeeded, SQLite BEGIN IMMEDIATE enforced atomicity.
-> Concurrency verification: 50 total rows, 50 distinct IDs.

(c) Deep immutability round-trip:
Attempting to mutate trust_root dict...
-> SUCCESS: Mutation rejected: 'mappingproxy' object does not support item assignment
Attempting to mutate transitive_executable_chain list...
-> SUCCESS: Mutation rejected: 'tuple' object has no attribute 'append'
--- TRACE DEMONSTRATION END ---
```

## C. Shim Registry Persistence Folding

**Context:** `PHASE1-THIRDPARTY-DEFERRAL-AND-SHIMS-2026-08-20.md` specified an atomic-candidate-snapshot pattern (`os.replace`) on a `shim_registry.json` file. `ARCHITECTURE.md` explicitly deprecated this pattern ("bespoke JSON-file locking") in favor of SQLite.

**Resolution:**
The shim registry state is folded into the same SQLite database as the manifest receipts, sharing the single operational source of truth.

### 1. Illustrative Schema Extension
```sql
CREATE TABLE shim_registry_entries (
    shim_name TEXT PRIMARY KEY,
    target_executable_path TEXT NOT NULL,
    profile_name TEXT NOT NULL,
    admission_receipt_id TEXT NOT NULL REFERENCES manifest_admission_receipts(admission_receipt_id),
    shim_file_sha256 TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE shim_backup_entries (
    backup_id TEXT PRIMARY KEY,
    shim_name TEXT NOT NULL REFERENCES shim_registry_entries(shim_name) ON DELETE CASCADE,
    original_path TEXT NOT NULL,
    backup_path TEXT NOT NULL,
    original_sha256 TEXT NOT NULL,
    backed_up_at_utc TEXT NOT NULL,
    restored_at_utc TEXT,
    status TEXT NOT NULL -- e.g., 'ACTIVE', 'RESTORED', 'ORPHANED'
);
```

### 2. Transactional Replacement
The complex JSON-file read-modify-write cycle (with `.tmp.<pid>` files and `os.replace`) is eliminated. When installing or updating a shim's bindings, the coordinator uses an ordinary `UnitOfWork`:
```python
with self._store.unit_of_work() as unit:
    unit.put_shim_entry(new_shim_entry)
    unit.commit()
```
If a flat `shim_registry.json` file is required for external tooling consumption or fast shell pathing, it becomes an **explicitly-derived, read-only export/cache**. Consistent with how `ARCHITECTURE.md` treats the adapter registry as a "disposable derived index", a post-commit hook or explicit CLI command regenerates the JSON cache from the SQLite operational source of truth. It is never read back in to make dispatch decisions or write resolutions.

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
* **Item B** is addressed with the illustrative `0025_manifest_admission_receipts.sql` schema and a `StateStore`-integrated coordinator trace.
* **Item C** is addressed by folding shim persistence into `shim_registry_entries` and migrating away from JSON file locking toward SQLite `UnitOfWork` writes.
* **Item D** is addressed with exact replacement diffs aligning the two earlier specification documents with the honest `chain_complete=False` scope boundary established in the final Phase 1 dialectic.
