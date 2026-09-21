# MECE Audit Remediation Status (2026-09-20)

Live remediation tracking for the 3-way MECE audit across Engram (`D:\Engram&Peerhub\engram-main-worktree`) and PeerHub (`P:\workspace\peerhub`).
Updated after each phase.

| Finding ID | Phase | Repo | Status | Commit Hash | Note |
|---|---|---|---|---|---|
| H-2 | E1 | Engram | DONE | 15da09c | Exclude backups and mutable runtime files (.jsonl) from package & gitignore; regression test added |
| M-4 | E2 | Engram | DONE | 0c82044 | Replace retired engram launch/start in docs with engram/engram open |
| L-1 | E2 | Engram | DONE | 0c82044 | Explicitly document PEERHUB_CONFIG_HOME as external tool environment bridge in docs & README |
| L-2 | E2 | Engram | DONE | 0c82044 | Update test count badge to dynamic/passing badge |
| L-3 | E2 | Engram | DONE | 0c82044 | Remove dead _sys/docs/history/ exemption from check_encoding.py |
| H-1 | E3 | Engram | DONE-UNCOMMITTED | - | Fix _sys renameability in backup, tidy, check_tool_updates, layout_migration; add tests |
| M-1 | E4 | Engram | TODO | - | Report actual process image in backup liveness and clarify Node-based AI CLI note |
| M-2 | E4 | Engram | TODO | - | Strict finite grammar validation in run_backup/restore/reset adapters (exit 2 on error) |
| M-3 | E4 | Engram | TODO | - | Allow backup standalone CLI without --out to use default <sys_dir>/data/backups |
| L-5 | E4 | Engram | TODO | - | Add engram.cmd-level forwarding tests for backup, restore, reset in test_engram_cmd_surface.py |
| M-5 | E5 | Engram | TODO | - | Fixture-isolate derived path constants in test_tidy.py |
| L-4 | E5 | Engram | TODO | - | Smoke contracts for shell test harnesses |
| L-6 | E5 | Engram | TODO | - | Saturation scan false positives against backup allowlist |
| P1-1 | P1 | PeerHub | DONE | (this commit) | Decouple routing import-capabilities from hardcoded _sys/ai path |
| P1-2 | P1 | PeerHub | DONE | (this commit) | Decouple quota_polling.py from hardcoded _sys paths with configurable fallback |

## Phase E3 Progress
- **E3a**: `_sys/checks/backup_personal_data.py` DONE-UNCOMMITTED.
  - Bootstrapped via `_sys/core/root.py:bootstrap_root_package` and removed hardcoded `base_dir / "_sys"`.
  - Backups and pre-restore snapshots routed to `sys_dir / "data" / "backups"`.
  - Added `test_backup_and_restore_renamed_sys_dir_safe`; all 35 unit tests pass green.
- **E3b / M-5**: `_sys/core/tidy_temp.py` and `test_tidy.py` DONE-UNCOMMITTED.
  - Derived paths routed through `_SYS_DIR` and made dynamically configurable via `configure_paths`.
  - Added real worktree safety guard and `test_tidy_renamed_sys_dir_safe`; all 5 tests pass green.
- **E3c**: `_sys/checks/check_tool_updates.py`, `_sys/core/migrate_ais_to_engram.py`, and `_sys/core/layout_migration.py` review DONE-UNCOMMITTED.
  - `check_tool_updates.py`: routed `ARCHIVE_ROOT` and `DISCOVERY_CACHE_PATH` through `state_paths` using `_SYS_DIR`, and updated bootstrap invocation path to use `_SYS_DIR.name`.
  - `migrate_ais_to_engram.py`: added `--sys-dir` CLI option and routed default `sys_dir` through `_SYS_DIR.name`.
  - Added `test_check_tool_updates_renamed_sys_dir_safe`; 20 unit tests pass green (`test_check_tool_updates.py` and `test_migrate_ais_to_engram.py`).
  - Deliberate keeps for `layout_migration.py`:
    - `layout_migration.py:79-82`: `merge_declarations` default paths kept as DEFERRED-NEEDS-REVIEW.
    - `layout_migration.py:229,234-236`: `_is_protected` release manifest path matchers (release zips always contain `_sys/`).
    - `layout_migration.py:293-295`: `_is_dir_protected` release folder cleanup guard (`_sys/env`, `_sys/tools`, `_sys/data`).
    - `layout_migration.py:524`: `run_pipeline` default fallback kept as DEFERRED-NEEDS-REVIEW.

## Terminal close-out (2026-09-21)
- E1 15da09c, E2 0c82044, E3 d1cc169/282a8ad/03d70a3, E4 0fd2aff (Engram main, suite 414 passed/3 skipped); P1 in the peerhub commit that adds this note (suite 1773 passed/5 skipped, pyright 0 errors).
- Still open: E5 L-4 (harness .bat/.ps1 smoke contract) and L-6 (saturation_scan noise) -- deferred, low priority. Engram v3.3.1 patch release pending cx.effort static review.
