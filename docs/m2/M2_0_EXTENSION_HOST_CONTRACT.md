# Extension Host Contract (M2.0)

## 1. Authority & Storage Layout
- **Authority:** The Core application is the supreme authority. Extensions are unprivileged guests. The Core package import graph statically forbids importing extension namespaces (Test EXT-002).
- **Storage Layout:**
  - `extension_registry.db`: Authoritative Core database storing manifest metadata and lifecycle state mappings.
  - `ext_{id}.db` (or strictly prefixed tables `ext_*`): Cryptographically/Logically isolated storage for each extension.
  - `extensions/`: Physical directory containing manifest JSONs and entrypoint Python files.

## 2. Port Protocols & Error Types
The Extension Host exposes a strictly typed API boundary:
- `discover(path) -> Manifest`: Scans directory, parsing metadata. Errors: `SchemaValidationError`, `SecurityBoundaryError`.
- `validate(manifest) -> bool`: Verifies static dependencies before code evaluation. Errors: `MissingDependencyError`, `TypeMismatchError`.
- `enable(ext_id)`: Registers module, triggers SQLite migration, and binds hooks. Errors: `ForbiddenTransitionError`, `MigrationCrashError`, `ExtensionHookError`.
- `disable(ext_id)`: Unloads from runtime registry, clears LRU caches, strictly preserves DB data. Errors: `ForbiddenTransitionError`.

## 3. Manifest JSON Schema
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["id", "version", "entrypoint"],
  "properties": {
    "id": { "type": "string", "pattern": "^ext_[a-z0-9_]+$" },
    "version": { "type": "string" },
    "entrypoint": { "type": "string" },
    "dependencies": { "type": "array", "items": { "type": "string" } },
    "description": { "type": "string" }
  },
  "additionalProperties": false
}
```

## 4. Ordering & Idempotency Rules
- **Idempotency:** Calling `enable(A)` when `A` is already `ENABLED` results in an immediate no-op returning the current state without DB mutations (Test EXT-009).
- **Ordering:** Discovery MUST precede Validation; Validation MUST precede Enablement.
- **Transactions:** Schema migrations run in strict, isolated SQLite WAL transactions. Concurrent enables block cleanly via timeouts (EXT-017).

## 5. Crash Matrix & Recovery
| Crash Point | Resulting State | Recovery Action | Covering Test |
| :--- | :--- | :--- | :--- |
| Mid-migration crash | DB torn state | SQLite WAL rolls back uncommitted TX. State safely reverts to previous on boot. | EXT-008 |
| Hook unhandled exception | Execution faults | Caught by Host boundary. Extension mapped to FAILED. Core safely continues. | EXT-013 |
| Missing Dependency | Process halts mid-validate | Fails validation. Extension mapped to FAILED. | EXT-014 |
| Core Reboot | In-memory cache lost | Host rebuilds state seamlessly from SQLite registry on boot. | EXT-012 |

## 6. Windows-Specific Rules
- **File locking:** Extensions cannot hold open file handles to Core directories.
- **SQLite Concurrency:** Uses strictly configured `busy_timeout` to handle Windows mandatory file locks during concurrent reads/writes (EXT-017).

## 7. Null/Empty/Unknown Semantics
- Unknown manifest keys strictly rejected (EXT-006).
- Null values for strings strictly rejected (EXT-019).
- Empty strings allowed only where schema explicitly permits (e.g., `description`).

## 8. Migration, Rebuild, Disable, Rollback
- **Disable:** Unloads modules (via `sys.modules` eviction & GC) and stops hooks. Data tables strictly preserved (EXT-004, EXT-016).
- **Rollback (Downgrade):** Strictly forbidden. Loading older manifest than DB version raises `DowngradeNotSupportedError` (EXT-024).

## 9. Security & Trust Boundary
- Core code never natively trusts extension output. Events are passed via strictly typed proxy interfaces.
- Extension DB connections run with restricted PRAGMA settings (e.g., no ATTACH database permitted on core DB).

## 10. Interaction List
- Core -> Host: `boot()`, `dispatch_event(Event)`
- Host -> DB: `read_registry()`, `update_state()`
- Host -> Extension: `run_migration()`, `invoke_hook(Event)`
