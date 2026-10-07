# Backup & Disaster Recovery Contract (M2.5)

## 1. Authority Separation & Core Invariants
- **Core Invariant 11: `Restore starts from authoritative and rebuilds derived`:**
  - **Authoritative State:** CoreStore SQLite DB (`core.db`), CAS Artifact blobs directory (`artifacts/`), and registered skill source directories (`skills/`).
  - **Derived / Ephemeral State:** Work projection SQLite DB (`work.db`), Skill index SQLite tables, staging and temporary directories (`.tmp/`). Derived views are NEVER backed up as truth and are ALWAYS rebuilt from authoritative state upon restoration.
  - **State Kind × Recovery Class:**
    - `FULL_COLD_RECOVERY`: Authoritative state restored -> integrity verified -> generation fence applied -> derived views rebuilt.
    - `PROJECTION_REBUILD`: Authoritative state untouched -> projection wiped -> replayed from authoritative records.
- **Generation Fencing:**
  - Every backup records an authoritative `generation` counter (epoch).
  - Restoration increments the generation fence, invalidating any concurrent or stale writers attempting to commit to older epochs.
- **Backup Manifest & Reopen Proof:**
  - A backup is invalid until `backup_manifest.json` is verified.
  - Manifest includes: `backup_id`, `created_at`, `generation`, `files` (relative path to SHA-256 and byte size), `records_count`, `secret_exclusion_policy` (`EXCLUDED_BY_DEFAULT`), and `reopen_proof`.
  - Reopen proof confirms that `core.db` passes `PRAGMA integrity_check` before the backup is committed.
- **Secret Exclusion Policy:**
  - Tokens, credentials, and API keys are strictly excluded or masked from the backup bundle by default.

## 2. Public Interfaces & Protocols
The Backup & Recovery engine (`peerhub.m2.backup`) provides typed public interfaces:
- `create_backup(authoritative_db: Path, artifact_dir: Path, target_dir: Path, generation: int) -> BackupManifest`
  Errors: `BackupCorruptedError`, `SecretLeakageDetectedError`.
- `verify_backup(backup_dir: Path) -> bool`
  Errors: `BackupCorruptedError`, `BackupManifestMissingError`.
- `restore_authoritative(backup_dir: Path, target_dir: Path, current_generation: int) -> int`
  Errors: `BackupManifestMissingError`, `BackupCorruptedError`, `GenerationFencingConflictError`.
- `rebuild_derived_projections(core_store: CoreStore, work_db_path: Path) -> int`
  Errors: `AuthoritativeRestoreOrderError`.

## 3. Backup & Restore State Machine
```text
[Backup Creation]
  STAGING -> INTEGRITY_CHECK -> MANIFEST_SEALED -> READY
                                      │ (failure)
                                      ▼
                                    FAILED

[Restore Sequence (Invariant 11)]
  VERIFY_MANIFEST -> RESTORE_AUTHORITATIVE -> GENERATION_FENCED -> REBUILD_PROJECTIONS -> RESTORED
                           │ (failure)
                           ▼
                        ABORTED
```
- Restoration strictly requires `RESTORE_AUTHORITATIVE` before `REBUILD_PROJECTIONS`.
- Any attempt to trigger projection rebuild or work state replay without verified authoritative state raises `AuthoritativeRestoreOrderError`.

---

## Closure update (2026-10-08)

Written after the M2/M3 closure work (`docs/implementation/M2_M3_CLOSURE_2026-10-08_KO.md`) and checked against the code and tests. Where it conflicts with the text above, **this section wins**. State-machine and exception JSON catalogs are updated separately.

Section 1: replace the derived-state and generation-fencing bullets:

```markdown
  - **Derived / Ephemeral State:** Derived extension databases and staging files are not restored as authoritative truth. `restore_authoritative` publishes restored authoritative state and excludes the prior workspace's derived databases. Rebuilding projections is a separate operation supplied by the composition root.
- **Generation Fencing:**
  - The backup records an integer generation. Restore also writes a fresh `workspace.generation` identity and a `restore.epoch`.
  - With an effective current generation of zero, the returned restore epoch is the backup generation; otherwise it is `max(backup_generation, current_generation) + 1`.
  - `allow_rollback` defaults to `True`; an older backup is rejected for its generation only when `allow_rollback=False`.
  - Existing CoreStore instances detect workspace replacement and raise `StoreReplacedError`. This is an offline restore: callers must stop all writers and release open connections before invoking it.
```

Section 2: correct the module path to `peerhub.extensions.backup`; replace these signatures:

```markdown
- `restore_authoritative(backup_dir: Path, target_dir: Path, current_generation: int = 0, allow_rollback: bool = True, *, fault_hook=None) -> int`
  Errors: `BackupManifestMissingError`, `BackupCorruptedError`, `GenerationFencingConflictError`, and filesystem errors.
- `rebuild_derived_projections(core_store, projection, *, page_size: int = 500) -> int`
  The caller supplies a public projection port exposing `rebuild_projection(records) -> int`; Backup does not import Work or construct a WorkProjection from a database path.
  Errors: `AuthoritativeRestoreOrderError`.
```

Section 3: replace the restore-sequence diagram and its two following bullets:

```markdown
[Offline Restore Sequence]
  VERIFY_MANIFEST -> STAGE_AND_VERIFY -> CHECK_WORKSPACE_DATABASES
    -> MOVE_PREVIOUS_WORKSPACE -> PUBLISH_RESTORED_WORKSPACE

[Separate Derived Recovery]
  AUTHORITATIVE_STORE_AVAILABLE -> REBUILD_PROJECTIONS
```

```markdown
- **Exclusive Boundary:** The replacement boundary is the entire managed workspace directory. Before moving an existing workspace, restore attempts `PRAGMA wal_checkpoint(TRUNCATE)` on every top-level regular file ending in `.db`, including extension and derived databases. A busy result or SQLite operational error raises `GenerationFencingConflictError`.
- **Offline Requirement:** These checks detect busy databases at check time; they do not provide a transactional fence against arbitrary processes starting work between the check and the directory swap.
- **Directory Replacement:** Restore retains the old workspace in a sibling `<name>.pre-restore-*` directory. Derived databases are discarded from the restored workspace, while the retained previous directory preserves them.
- **Crash Recovery:** A durable intent marker is written before moving the previous workspace. If a hard crash leaves the target absent, `recover_interrupted_restore` restores the previous directory, including all its databases. If the target already exists, recovery removes a stale marker without reverting the restored workspace.
- **Projection Port:** Rebuild requires an existing authoritative CoreStore database, streams all pages of each stream in position order, and delegates to the supplied projection port. Missing authority or replay/delegation failure raises `AuthoritativeRestoreOrderError`.
- **Ordering Scope:** Rebuild checks authoritative-store availability; it does not require a successful prior restore or a persisted restore-state-machine token.
```

**Wrong/obsolete:** automatic rebuilding inside restore; Core-only exclusivity; live-process transactional fencing; unconditional numeric increment; older-backup rejection by default; the `work_db_path` rebuild argument; and reverting a completed restore solely because an intent marker remains.
