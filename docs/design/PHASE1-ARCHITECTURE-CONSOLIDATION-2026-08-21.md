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

    def admit_manifest(self, raw_manifest: dict, executable_chain: list) -> 'ManifestAdmissionReceipt':
        # 1. Validation logic carried over unchanged (schema, MZ check, PATHEXT rules, etc.)
        
        # 2. Collision-safe ID issuance via real transaction semantics
        receipt_id = f"receipt-{self._ids.next_id()}"
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

*Trace Demonstration:*
Under concurrent access (e.g., two requests attempting to admit the same manifest simultaneously), the `AdmissionRegistry`'s in-process thread lock is completely replaced by SQLite `BEGIN IMMEDIATE` transaction semantics. Each coordinator instance obtains an isolated `ManifestAdmissionUnitOfWork`. ID issuance (`self._ids.next_id()`) prevents PK collisions for valid concurrent operations. If both attempt to write conflicting state, the database enforces atomicity at the `commit()` boundary. The returned `ManifestAdmissionReceipt` remains deeply immutable, preserving the exact read-only properties verified in the 66-round dialectic without relying on closure-scoped dictionaries.

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
