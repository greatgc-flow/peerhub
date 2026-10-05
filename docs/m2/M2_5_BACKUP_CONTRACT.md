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
