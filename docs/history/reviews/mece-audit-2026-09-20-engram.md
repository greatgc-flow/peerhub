# MECE Audit — Engram (2026-09-20)

**Reviewer:** cx.effort (single independent pass; the planned cross-review exchange with ag.opus and ag.effort could not complete — both hit `RESOURCE_EXHAUSTED`/health failures mid-exchange, per cx.effort's own report. Copied here from `P:\_sys\data\temp\mece-audit-2026-09-20-engram.md`, where cx.effort actually saved it after its designated peerhub write path was blocked by its own sandbox policy.)

**Scope:** `D:\Engram&Peerhub\engram-main-worktree` only. Read-only source/docs/tests review plus the repo's own `check_unreferenced_functions.py`/`check_root_hygiene.py`/`check_encoding.py`/`saturation_scan.py` static tools. No product files were changed.

## Initial verdict

The release has a coherent public command surface and the static dead-code gate is clean. However, the claim of *full* `_sys` folder renameability is not true across the shipped command surface: the just-added backup/restore/reset implementation and pre-existing `tidy`/check/update code still construct literal `_sys` paths in places the Phase 1-4 rename work didn't reach. The default backup directory is also neither gitignored nor excluded from portable packaging.

## Findings

### H-1 — renameability is incomplete in live commands
- `_sys/checks/backup_personal_data.py` (never touched by this session's Phase 1-4 rename work — a real gap, not a partial fix): writes default/pre-restore backup zips to `base_dir / "_sys" / "data" / "backups"` at multiple call sites, ignoring the actual (possibly renamed) `sys_dir` passed in.
- `_sys/core/tidy_temp.py`: despite Phase 3 converting its *bootstrap* self-location, several of its cleanup-target derivations still independently construct `ROOT / "_sys"` for other purposes beyond the initial bootstrap.
- `_sys/checks/check_tool_updates.py`: state-path and bootstrap-command construction still use a literal `_sys` in places beyond its Phase 1a bootstrap call.
- `_sys/core/migrate_ais_to_engram.py`, `_sys/core/layout_migration.py`: retain literal-name filesystem paths; not part of any Phase 1-4 dispatch scope.
- No renamed-system-directory test exists for `tidy`, backup/restore/reset, migration, or check-tool-updates (only entrypoint discovery and updater/uninstaller remapping are tested that way).

### H-2 — package filtering already ships mutable runtime data and will admit future backups
`engram backup` defaults to `_sys/data/backups/*.zip`. Neither `.gitignore` nor `tools/winget/build_package.py`'s exclude patterns cover `backups`; `test_winget_manifests.py` only asserts exclusion of `state`/`logs`/`temp`. **Not hypothetical**: the already-shipped v3.3.0 archive and its committed `release-manifests/3.3.0.json` both contain `_sys/data/operational_errors.jsonl`, a mutable local runtime record. A future build can similarly ship user-created backup files (personal transcripts/settings) — contrary to the "zero-bloat"/credential-safety intent of the backup allowlist itself.

### M-1 — backup liveness detection can falsely label Node as Codex
`check_running_processes()`'s reused `provisioner.py` process-name map treats any `node.exe` (Codex's own process shape) as evidence Codex is running, potentially causing false-positive backup warnings/restore-refusals if an unrelated Engram-owned `node.exe` is active.

### M-2 — path/flag validation is not a command-level contract
`run_backup`/`run_restore`/`run_reset` silently accept unrelated/unknown flags rather than a defined finite grammar; README's exit-code-2 usage-error promise for `restore` doesn't match current adapter behavior for most malformed invocations.

### M-3 — standalone backup documentation promises a default the code rejects
`backup_personal_data.py`'s docstring says `--backup [--out PATH]` defaults to a standard location, but `main()` actually requires `--out` explicitly for standalone invocation (`parser.error` if missing) — a real, test-codified contradiction (`test_main_backup_without_out_errors`) between the docstring and the standalone CLI's own required-argument behavior. (The first-class `engram backup` adapter path does correctly default.)

### M-4 — documentation still advertises retired command names
`docs/engram-dotdir.md` and `CONVENTION.md` still describe `engram launch`/`engram start` as live syntax; both are retired verbs that exit 2 per `engram.cmd` and README's own retired-verb table.

### M-5 — `tidy` tests aren't isolated from real derived paths
`test_tidy.py` patches only `tidy_temp.ROOT`; other module-level path constants (npm/pip cache, VS Code cache, `DATA_TEMP_DIR`) are derived at import time against the real worktree and aren't fixture-isolated in `--apply` tests.

### L-1 — stale peerhub coupling remains in Engram-facing docs/config
README/docs describe Engram as fully separated from peer collaboration, yet `docs/engram-dotdir.md` and `_sys/env.json`'s `PEERHUB_CONFIG_HOME` injection still present PeerHub config as part of Engram's managed root, unexplained as an intentional compatibility bridge.

### L-2 — README's test-count badge is stale
README claims "330" tests; `pytest --collect-only` measured 406, consistent with this session's actual additions. (Full pytest couldn't run in cx.effort's own sandbox due to an unrelated `WinError 5` basetemp permission issue — an environment artifact, not a product defect; the terminal has independently verified 403 passed/3 skipped/0 failed multiple times this session on the real repo.)

### L-3 — residual migration-era checker exclusion
`check_encoding.py` still exempts a `_sys/docs/history/` path that no longer exists after this session's history migration. Harmless cleanup debt.

### L-4/L-5 — incomplete shell/entrypoint test coverage
Several tracked `.bat`/`.ps1` test-harness files have no unit-test reference at all; `test_engram_cmd_surface.py` has no dedicated `backup`/`restore`/`reset` forwarding case through the root `engram.cmd` entrypoint (only the adapter layer is tested for those three).

### L-6 — `saturation_scan.py` is noisy and misses the real rename regressions
Returns 24 findings, mostly false positives against the new backup allowlist's intentional literal paths (e.g. `claude/CLAUDE.md`), while missing the actual `ROOT / "_sys"` regressions named in H-1.

## Cross-review outcome (per cx.effort's own report)
Both `ag.opus` and `ag.effort` were sent the initial report and a critique request; neither completed a reply (`RESOURCE_EXHAUSTED`/stalled-subscriber). Findings stand as a single independent pass, not a converged 3-way review.

## Verification record (cx.effort, this session)
- `check_unreferenced_functions.py`: clean (0 candidates).
- `check_root_hygiene.py`: clean. `check_encoding.py --all`: clean.
- `pytest --collect-only`: 406 collected. Full pytest blocked by a sandbox-only `WinError 5` (not a product issue).
- `saturation_scan.py --sys-root _sys --force`: exit 1, findings summarized in L-6.
