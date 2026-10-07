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
- **Boot & Discovery Timing:** At Host boot (`boot()`), all extension manifests in `extensions/` are parsed into a lightweight in-memory metadata index. However, zero Python code is imported or instantiated for extensions that are disabled or unvalidated. Code evaluation only occurs upon explicit `enable()` (Decision #1).
- **Transactions:** Schema migrations run in strict, isolated SQLite WAL transactions. Concurrent enables block cleanly via timeouts (EXT-017).
- **Version Dependencies:** Manifest dependencies require exact-version matches only; SemVer constraint solvers are explicitly non-goals for M2.0 (Decision #8). Unmet dependencies reject with `MissingDependencyError`.

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
- **Rollback (Downgrade):** Strictly forbidden. Loading an older manifest than the DB's recorded `schema_version` immediately raises `DowngradeNotSupportedError` and halts loading, mirroring M1's strict Exit 6 policy (Decision #4, EXT-024).

## 9. Security & Trust Boundary
- Core code never natively trusts extension output. Events are passed via strictly typed proxy interfaces.
- Extension DB connections run with restricted PRAGMA settings (e.g., no ATTACH database permitted on core DB).

## 10. Interaction List
- Core -> Host: `boot()`, `dispatch_event(Event)`
- Host -> DB: `read_registry()`, `update_state()`
- Host -> Extension: `run_migration()`, `invoke_hook(Event)`

---

## Closure update (2026-10-08)

Written after the M2/M3 closure work (`docs/implementation/M2_M3_CLOSURE_2026-10-08_KO.md`) and checked against the code and tests. Where it conflicts with the text above, **this section wins**. The state-machine and exception JSON catalogs carry the same update.

Section 2: replace the nonexistent `validate(manifest)` interface and revise `enable`/add `boot`:

```markdown
- `validate_manifest(data) -> ExtensionManifest`: Validates manifest fields and dependency syntax without importing extension code. Errors: `SchemaValidationError`.
- `boot() -> dict[str, Any]`: Discovers manifests under the configured `extensions_dir` and rechecks persisted ENABLED extensions. Returns `discovered`, `errors`, and `failed`.
- `enable(ext_id) -> str`: Checks dependencies and entrypoint availability, advances lifecycle state, and loads the entrypoint. Errors: `MissingDependencyError`, `ForbiddenTransitionError`, `ExtensionHookError`. Schema migration and hook registration are separate operations.
```

Section 3: replace the `dependencies` property:

```json
"dependencies": {
  "type": "array",
  "items": {
    "type": "string",
    "pattern": "^ext_[a-z0-9_]+(?:==[A-Za-z0-9][A-Za-z0-9.+_-]*)?$"
  }
}
```

Section 4: replace **Boot & Discovery Timing** and **Version Dependencies**, then add:

```markdown
- **Boot & Discovery Timing:** At `boot()`, the Host scans immediate child directories of the configured `extensions_dir` containing `manifest.json`, in sorted order. Each discovery failure is reported in `errors` and does not prevent discovery of other extensions. Boot discovery does not import extension entrypoints. A valid persisted ENABLED state is retained without reconstructing its runtime module.
- **Version Dependencies:** Dependencies accept `ext_id` (any registered version) or `ext_id==exact.version` (identical registered version string). Every dependency must be installed and ENABLED. Ranges, whitespace-bearing specifications, empty pins, and dependency solvers are unsupported. Invalid syntax raises `SchemaValidationError`; unmet dependencies raise `MissingDependencyError`.
- **Fail-Closed Boot:** An ENABLED registry entry without an available in-memory manifest is changed to FAILED. ENABLED extensions with missing entrypoints or unmet dependencies are also changed to FAILED. Dependency checks repeat until no further extension fails, including dependents that sort before their failed dependency.
- **Registered Version Identity:** Discovery does not silently adopt a manifest version different from the version stored in the registry; it raises `RegistrationConflictError`.
- **Missing Entrypoint:** Enabling a newly discovered extension whose entrypoint file is missing raises `ExtensionHookError`, records FAILED, and does not register a loaded module.
```

Section 5: replace the **Missing Dependency** and **Core Reboot** rows; add the remaining rows:

```markdown
| Missing Dependency during enablement | Dependency is absent, disabled, or does not match an exact pin | Reject enablement with `MissingDependencyError`; a newly discovered extension becomes FAILED. | test_ext_026_dependency_forms |
| Core Reboot | Runtime modules and hooks are absent | `boot()` rediscovers metadata and rechecks persisted ENABLED states without importing entrypoints. | test_ext_027_boot_fail_closed |
| Invalid manifest during boot | One discovery fails | Report the directory in `errors`; continue discovering other extensions. | test_ext_025_boot_discovery |
| Missing entrypoint during enablement | Valid manifest has no module file | Raise `ExtensionHookError`; record FAILED without a loaded module. | test_ext_028_missing_entrypoint |
| Dependency failure during boot | ENABLED chain contains an unavailable dependency | Repeat checks until every affected ENABLED dependent becomes FAILED. | test_ext_027_boot_fail_closed |
| Manifest version changed | Disk version differs from registered version | Reject discovery with `RegistrationConflictError`; do not replace registered version silently. | test_ext_029_registered_version |
```

**Wrong/obsolete:** “exact-version matches only” excludes implemented unpinned dependencies; “rebuilds state seamlessly” implies runtime restoration that boot does not perform; `enable()` does not itself migrate schemas or bind hooks. Also, do not describe dependency failure as halting the process.

A verified implementation limitation worth preserving: the transition table does **not** allow `DISABLED -> FAILED`. Consequently, do not generalize the newly discovered missing-entrypoint/dependency failure guarantees to every possible source state.
