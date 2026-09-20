# Cross-Repo MECE Boundary Audit: PeerHub & Engram (2026-09-20)

**Reviewer:** ag.effort (completed solo pass; independent cross-verification incorporating and expanding upon prior passes by `cx.effort` on Engram and `ag.opus` on PeerHub).  
**Scope:** Cross-repo boundary analysis between **PeerHub** (`P:\workspace\peerhub`, remote: `greatgc-flow/peerhub`, v0.6.0) and **Engram** (`D:\Engram&Peerhub\engram-main-worktree`, remote: `greatgc-flow/Engram`, v3.3.0). Read-only source/git analysis across both codebases.

---

## 1. Executive Summary & Verdict

The physical separation between Engram (the portable Windows developer runtime product) and PeerHub (the multi-AI CLI coordination and governance package) is functionally established at the repository and packaging levels. However, this comprehensive MECE audit reveals asymmetric architectural boundaries:

1. **History Migration Completeness (Clean):** 100% of the 200 historical AI collaboration files removed from Engram in commit `2c19d77` are verified **byte-identical (SHA-256)** in `peerhub/docs/history/from-engram-repo/`. The 3 files omitted from migration were backlog-specific verification scripts retired intentionally alongside `backlog.json`.
2. **Version & Doc Reference Staleness (Clean):** Grepping across both repositories for prior versions (`0.5.0` for PeerHub, `3.2.7` for Engram) confirmed zero stale references. All occurrences are legitimate changelog comparisons, Winget historical release manifests, backwards-compatibility fallback definitions, or design specs.
3. **Engram Findings Verification (H-1 & H-2 Confirmed):** Independent verification of `cx.effort`'s findings confirms both HIGH findings. `_sys` renameability remains broken in newly introduced and legacy commands (`backup_personal_data.py`, `tidy_temp.py`, `check_tool_updates.py`, `migrate_ais_to_engram.py`), and the Winget packaging pipeline (`build_package.py`) failed to exclude `_sys/data/backups/` and actually shipped the mutable runtime file `_sys/data/operational_errors.jsonl` in the released `Engram-v3.3.0-portable-x64.zip` and manifest. Medium findings M-1 (Node.js misidentified as Codex), M-3 (CLI docstring argument mismatch), M-4 (retired verb `launch`), and Low findings L-2 (test count badge stale at 330 vs 406 actual) and L-3 (dead exclusion in `check_encoding.py`) are also confirmed.
4. **Separation Boundary & Asymmetric Coupling (Action Needed):** While Engram contains only an intentional, documented compatibility bridge (`PEERHUB_CONFIG_HOME` in `_sys/env.json`), **PeerHub still harbors critical hardcoded assumptions about Engram's old unified layout**. Specifically, `peerhub/cli/__init__.py` attempts to reach `workspace_root.parent.parent / "_sys" / "ai"`, and `peerhub/telemetry/quota_polling.py` retains legacy hardcoded paths into `_sys/env/nodejs/npm-global` and `_sys/claude/config`.
5. **Git Architecture & Path Hazards:** The worktree topology (`engram-main-worktree` sharing the `.git` object store with `PortableDev (v2.1)`) and the physical path containing an ampersand (`D:\Engram&Peerhub\...`) create real operational hazards (`cmd.exe /c` command splitting, checkout locks, and risk of accidental repository wiping by Engram cleanup commands targeting `workspace/`).

---

## 2. Dimension 1: History Migration Byte-Verification

In Engram commit `2c19d77` ("chore: migrate AI-collaboration history to peerhub repo"), 203 files were removed. In PeerHub commit `be22db1` ("docs(history): archive AI-collaboration history migrated from Engram"), 201 files were committed under `docs/history/from-engram-repo/`.

A systematic SHA-256 byte-by-byte hash comparison was conducted between every file in Engram at commit `2c19d77~1` (`dca195a`) and its corresponding file in `peerhub/docs/history/from-engram-repo/`:

| Migration Source in Engram (`2c19d77~1`) | Destination in PeerHub (`docs/history/from-engram-repo/`) | File Count | SHA-256 Hash Match |
|---|---|---|---|
| `_sys/docs/history/**` | `protocol-docs/**` | 160 | **160 / 160 (100% Identical)** |
| `_sys/data/sessions/**` | `sessions/**` | 38 | **38 / 38 (100% Identical)** |
| `_sys/data/proposals/**` | `proposals/**` | 1 | **1 / 1 (100% Identical)** |
| `_sys/data/backlog.json` | `backlog.json` | 1 | **1 / 1 (100% Identical)** |
| **Total Migrated Content** | | **200** | **200 / 200 (100% Byte-Identical)** |

### Accounting for Non-Migrated and Additional Files
- **3 Files Deleted in Engram but Not Migrated:**
  1. `_sys/checks/check_backlog.py`
  2. `_sys/tests/unit/test_check_backlog_freshness.py`
  3. `_sys/tests/unit/test_check_backlog_nulls.py`  
  *Analysis:* These were active CI checks and unit tests enforcing formatting and freshness rules specifically for `_sys/data/backlog.json`. When `backlog.json` was migrated out of Engram as historical archive material, these checks were intentionally retired from Engram rather than copied as living tests into PeerHub.
- **1 Additional File in PeerHub:**
  `docs/history/from-engram-repo/README.md`  
  *Analysis:* Documents the provenance, migration rationale, and git commit references (`dca195a` -> `2c19d77`).

**Verdict:** Complete and verified. Zero corruption, zero truncation, and zero missing migration artifacts.

---

## 3. Dimension 2: Cross-Repo Version & Reference Staleness

A full repository grep was performed across both repos targeting the prior version identifiers: **`0.5.0`** (PeerHub previous release) and **`3.2.7`** (Engram previous release).

### PeerHub Grep Results
- **`0.5.0` Hits:**
  - `docs/STATUS.md:50`: Explains gateway convergence enhancements relative to `v0.5.0` ("every remaining gap v0.5.0 left open is now closed"). *Legitimate changelog comparison.*
  - `docs/STATUS.md:52`: Header `**Released as v0.5.0 (2026-09-20).**` *Legitimate historical release record.*
  - Generated test captures under `tools/phase0_fixture_runner/captures/`: SHA-256 hex digests containing the random substring `050`. *False positive (filtered).*
- **`3.2.7` Hits:**
  - Zero occurrences in source or documentation.

### Engram Grep Results
- **`0.5.0` Hits:**
  - Precompiled binaries (`_sys/tools/fd/fd.exe`, `_sys/tools/oh-my-posh/oh-my-posh.exe`): internal version strings.
  - `docs/design/engram-ux-simplification-RATIFIED-2026-09-12.md:48` and proposals: Historical changelog documenting the update of `fd` from `10.4.2` to `10.5.0`. *Tool pin note, unrelated to PeerHub.*
- **`3.2.7` Hits (22 occurrences):**
  - `_sys/core/uninstaller.py:34`: `# Constant list of top-level _sys program entries for v3.2.7 (Ratified §6.1)`. Sets `V326_SYS_PROGRAM_ENTRIES` for backwards-compatible uninstall of pre-manifest installations. *Deliberate fallback logic.*
  - `_sys/tests/unit/test_uninstall_semantics.py:70, 187`: Unit tests validating uninstaller behavior against the v3.2.7 constant program set. *Active regression tests.*
  - `manifests/g/greatgc-flow/Engram/3.2.7/*`: Official committed Winget release manifests for v3.2.7 (`greatgc-flow.Engram.installer.yaml`, locale files). *Immutable package archive records.*
  - `docs/design/engram-ux-simplification-RATIFIED-2026-09-12.md`: Ratified specification for the v3.2.7 emergency hotfix (uninstall safety and wrapper exit codes). *Authoritative design history.*

**Verdict:** Zero stale version references found in either codebase. All instances are proper historical records, backwards-compatibility invariants, or binary/tool versions.

---

## 4. Dimension 3: Separation-Boundary Claims & Coupling Analysis

The architectural boundary was audited in both directions to verify whether either system makes invalid assumptions about the other.

### A. Engram -> PeerHub Boundary: Intentional Compatibility Bridge
`cx.effort`'s finding L-1 flagged `_sys/env.json` line 19 for injecting `PEERHUB_CONFIG_HOME`:
```json
"PEERHUB_CONFIG_HOME": {"base": "engram", "sub": "peerhub/config"}
```
**Audit Analysis:**
- Investigation of `peerhub/adapters/codex_adapter.py:83-90` and `docs/engram-dotdir.md` reveals that this injection is **an intentional, ratified compatibility bridge** (Ratified Dotdir Consolidation 2026-09-09, items 11 & 13).
- Purpose: When a user operates within an Engram portable environment, Engram ensures that all managed AI tools (Claude, Codex, Antigravity, GitHub CLI) and PeerHub store their global configuration under `<portable-root>/.engram/` rather than writing credentials and caches to the host user's `%USERPROFILE%`.
- Isolation: Engram's core runtime does NOT import, execute, or package PeerHub. In `_sys/tests/unit/l1_core/test_contracts.py:132-135`, Engram enforces a strict contract:
  ```python
  assert "peerhub" not in tools, "peerhub must not be a runtimes.json-managed tool"
  assert cfg.get("install_mechanism") != "pip_tool"
  ```
- **Documentation Gap:** Although functionally intentional, `README.md` and `docs/engram-dotdir.md` present `PEERHUB_CONFIG_HOME` without explicitly noting that Engram has zero dependency on PeerHub, leaving users and reviewers uncertain about whether this is stale coupling.

### B. PeerHub -> Engram Boundary: Genuinely Stale Coupling Found
While Engram is cleanly isolated from PeerHub, **PeerHub contains multiple live code paths that hardcode assumptions about Engram's legacy internal file layout**:

1. **Severe Directory Traversal Assumption (`peerhub/cli/__init__.py:1978`):**
   ```python
   if parsed.routing_action == "import-capabilities":
       sys_root = workspace_root.parent.parent / "_sys" / "ai"
       protocol_path = Path(parsed.protocol or sys_root / "protocol.json").resolve()
       orchestration_path = Path(parsed.orchestration or sys_root / "orchestration.json").resolve()
   ```
   *Impact:* When running `peerhub routing import-capabilities`, PeerHub assumes it is executed from a workspace located at `<portable-root>/workspace/<subfolder>` and traverses `parent.parent` looking for `<portable-root>/_sys/ai`. In a standalone installation (e.g. `C:\Users\Alice\Projects\my-repo`), this resolves to `C:\Users\_sys\ai` or fails outright.

2. **Engram-Specific Portable Layout in Quota Polling (`peerhub/telemetry/quota_polling.py`):**
   - **Line 36-37 (`_resolve_sys_dir`):** Falls back to `Path.cwd() / "_sys"` if not found, assuming an Engram root.
   - **Lines 180-184 (`_real_binary`):**
     ```python
     cand = resolved_sys / "env" / "nodejs" / "npm-global" / CLAUDE_CMD
     ```
     Assumes the exact binary directory layout used by Engram's portable Node.js environment.
   - **Line 278 (Claude Quota Polling):**
     ```python
     env["CLAUDE_CONFIG_DIR"] = str((resolved_sys / "claude" / "config").resolve())
     ```
     Directly forces `CLAUDE_CONFIG_DIR` to point to `_sys/claude/config` — which is not even valid in modern Engram (which uses `.engram/claude/`) and violates standalone host environments.
   - **Line 629 (Antigravity Statusline):**
     ```python
     path = resolved_sys / "data" / "temp" / "ag_statusline_stdin.log"
     ```
     Hardcodes Engram's internal temp log location.

3. **Legacy Directive & Knowledge Paths:**
   - `peerhub/application/direct_ask.py:228`:
     ```python
     user_dir_path = request.workspace_root / "_sys" / "ai" / "user-directives.md"
     ```
   - `peerhub/application/lesson_inject.py:113`:
     ```python
     pack_path = "_sys/ai/knowledge/general/active-lessons.jsonl"
     ```
   - `tools/surface_manifest/generate_manifest.py:24`:
     ```python
     DEFAULT_SYS_DIR = Path("P:/_sys")
     ```

**Verdict:** The separation boundary is clean on the Engram side, but PeerHub still carries substantial legacy debt that assumes the host environment is an Engram/PortableDev tree.

---

## 5. Dimension 4: Independent Verification of Engram H-1 and H-2 Findings

We independently re-examined the source code, build scripts, and committed release assets for every finding reported by `cx.effort`.

### Finding H-1: `_sys` Renameability Incomplete in Live Commands
**Status: FULLY CONFIRMED**

The Phase 1-4 renameability initiative aimed to allow `_sys` to be renamed (e.g. for custom deployments or collision avoidance). Independent inspection confirms that multiple active command implementations continue to hardcode literal `"_sys"` paths:

| File | Line Citations | Evidence & Concrete Failure Mode |
|---|---|---|
| `_sys/checks/backup_personal_data.py` | Line 238, 369, 567 | `do_backup()` accepts `sys_dir`, but line 238 overrides it: `backups_dir = base_dir / "_sys" / "data" / "backups"`. Line 369 in `do_restore()` does the same for pre-restore snapshots. Line 567 in `main()` hardcodes `sys_dir = base_dir / "_sys"`. If `_sys` is renamed, backups and restore snapshots fail or write to a ghost `_sys/` folder. |
| `_sys/core/tidy_temp.py` | Lines 40, 85, 86, 92, 100, 104, 211, 214, 220, 271–275 | While line 28 derives `_SYS_DIR` dynamically, line 40 hardcodes `DATA_TEMP_DIR = ROOT / "_sys" / "data" / "temp"`. Lines 85-104 hardcode npm/pip/vscode/brain caches using `ROOT / "_sys"`. Lines 211-220 scan `(ROOT / "_sys").rglob(...)`. |
| `_sys/checks/check_tool_updates.py` | Lines 34, 35, 358 | Lines 34–35 construct `ARCHIVE_ROOT` and `DISCOVERY_CACHE_PATH` using `_PORTABLE_ROOT / "_sys"`. Line 358 executes `[r".\_sys\core\bootstrap.bat", "--skip-update"]`. |
| `_sys/core/migrate_ais_to_engram.py` | Line 150 | In `main()`, hardcodes `sys_dir = base_dir / "_sys"`. |
| `_sys/core/layout_migration.py` | Lines 79–82, 229–236, 293–295 | Hardcodes `defaults_dir = Path("_sys/defaults")`, `live_dir = Path("_sys")`, and migration paths under `_sys/`. |

### Finding H-2: Package Filtering Ships Mutable Runtime Data & Admits Backups
**Status: FULLY CONFIRMED (Not Hypothetical)**

1. **Packaging of Mutable Runtime Data in Released v3.3.0 Archive:**
   - Inspection of `_sys/core/release-manifests/3.3.0.json` (line 42) confirms:
     ```json
     "_sys/data/operational_errors.jsonl": "3B205657C694EB97816E6D85138D17CF8709F04D2AF4466E7187AE0238C07C1D"
     ```
   - Direct zip inspection of `dist/Engram-v3.3.0-portable-x64.zip` confirmed `_sys/data/operational_errors.jsonl` was packaged and distributed inside the actual zip release.
2. **Admission of User Backups in Future Builds:**
   - In `tools/winget/build_package.py`, `SYS_EXCLUDE_DIR_PATTERNS` excludes `logs`, `state`, `setup-files`, `.pytest_cache`, and `release-manifests`.
   - **`backups` is absent from both `SYS_EXCLUDE_DIR_PATTERNS` and `GLOBAL_EXCLUDE_PATTERNS`.**
   - Engram's root `.gitignore` ignores `_sys/data/logs/`, `_sys/data/state/`, and `_sys/data/temp/`, but does NOT ignore `_sys/data/backups/` or `_sys/data/operational_errors.jsonl`.
   - Any user backup `.zip` files residing in `_sys/data/backups/` will be bundled into future packages built with `build_package.py`.

### Spot-Check Verification of Remaining cx.effort Findings
- **M-1 (Backup liveness detects Node as Codex): CONFIRMED.** In `_sys/core/provisioner.py:924-925`, `process_names` for `codex` / `cx` is `("codex.exe", "node.exe")`. Line 947 checks if `exe.startswith(str(sys_dir.resolve()))`. Because Node runs from `_sys/env/nodejs/node.exe`, any running Node process triggers `_is_peer_leased(sys_dir, "codex") == True`, causing `backup_personal_data.py:360-364` to falsely abort `engram restore` with: `Cannot restore: managed AI CLI process(es) currently running: codex.exe`.
- **M-3 (Docstring vs CLI contradiction in backup): CONFIRMED.** `backup_personal_data.py:37` docstring advertises `--backup [--out PATH]`, but line 570 in `main()` enforces:
  ```python
  if args.backup and not args.out:
      parser.error("--backup requires --out PATH")
  ```
- **M-4 (Retired command names in docs): CONFIRMED.** `docs/engram-dotdir.md:9` still references `engram launch`, which exits 2 per `engram.cmd`.
- **L-2 (Stale test-count badge): CONFIRMED.** README.md line 8 displays badge `Tests: 330 green`. An independent run of `pytest _sys/tests/unit --collect-only -q` collected **406 tests**.
- **L-3 (Residual history exclusion in check_encoding): CONFIRMED.** `_sys/checks/check_encoding.py:58` continues to exclude `_sys/docs/history/`, which was completely removed in commit `2c19d77`.

---

## 6. Dimension 5: Git Topology & Operational Risks

### Topological Structure
```
[Host Storage: D:\]
 └── D:\Engram&Peerhub\PortableDev (v2.1)\              <- Root Git Repository (.git)
      │                                                   Branch: stable/hub-py-restored
      │                                                   Remote: greatgc-flow/Engram.git
      ├── [subst P:\] ─────────────────────────────────── Maps P:\ to PortableDev root
      │
      ├── .git/worktrees/engram-main-worktree/        <- Worktree metadata
      │
      ├── workspace/                                  <- Gitignored by root repo
      │    └── peerhub/                               <- Independent Git Repository (.git)
      │         ├── .git/                             <- Remote: greatgc-flow/peerhub.git
      │         └── ...                               <- Branch: main (v0.6.0)
      │
      └── (external worktree location)
           └── D:\Engram&Peerhub\engram-main-worktree\ <- Linked Git Worktree
                ├── .git (pointer file)                <- Branch: main (v3.3.0)
                └── ...                                <- Standalone Engram product
```

### Operational Risks Evaluated

1. **Shared Object Database Hazards:**
   - `engram-main-worktree` and `PortableDev (v2.1)` share the exact same `.git/objects` and `.git/refs` hierarchy.
   - Any aggressive maintenance command (`git prune`, `git gc --prune=now`, or branch deletions) executed in one worktree alters the underlying store for both.
   - Simultaneous checkout lock: Git prevents checking out `main` in `PortableDev` while `engram-main-worktree` is on `main`.
2. **Ampersand (`&`) Command-Splitting Hazard:**
   - The physical folder path contains an ampersand: `D:\Engram&Peerhub\...`.
   - In Windows `cmd.exe`, `&` is a command separator. If any script, tool, or subprocess invokes `cmd.exe /c` with an unquoted path resolved from `Path.resolve()`, `cmd.exe` truncates the command at `D:\Engram` and attempts to run `Peerhub\...` as a separate command.
   - This risk is currently mitigated in several files by avoiding `.resolve()` on junction paths (e.g. `peerhub/telemetry/quota_polling.py:193-195`), but remains an active latent hazard for any new script or test runner.
3. **Subst Drive (`P:\`) Identity Mismatch:**
   - In Windows, native executables can observe their working directory as `P:\...` while Python or Node `.resolve()` resolves it to `D:\Engram&Peerhub\PortableDev (v2.1)\...`.
   - This previously caused session cache key divergence in Antigravity (`last_conversations.json`), and requires constant diligence across both codebases.
4. **Nested Repo Destruction Hazard (`workspace/peerhub`):**
   - Because `peerhub` is nested inside `PortableDev/workspace/peerhub`, destructive cleanup commands in Engram (`engram uninstall --purge-data` or `engram reset --all`) target `workspace/`.
   - If executed without protections, this would permanently delete the local PeerHub working copy and its unpushed commits.
   - *Mitigation status:* Engram's uninstaller and reset commands currently enforce typed-confirmation guards (§6.2), but the physical nesting remains a structural risk.

---

## 7. Actionable Findings & Recommendations

All findings from this audit are summarized below for terminal disposition. (In accordance with review instructions, source code was not modified during this audit).

| ID | Repo | Sev | Location | Description & Proposed Remedy |
|---|---|---|---|---|
| **REC-01** | Engram | **HIGH** | `tools/winget/build_package.py:132-148`, `.gitignore` | **Fix package exclusion leak (Finding H-2):** Add `"backups"` to `SYS_EXCLUDE_DIR_PATTERNS`, add `_sys/data/*.jsonl` and `_sys/data/backups/` to Engram's root `.gitignore`, and prune `_sys/data/operational_errors.jsonl` from future packaging builds. |
| **REC-02** | Engram | **HIGH** | `_sys/checks/backup_personal_data.py:238, 369, 567` | **Honor dynamic `sys_dir` in backup/restore (Finding H-1):** Replace literal `base_dir / "_sys"` with `sys_dir / "data" / "backups"` in `do_backup()` and `do_restore()`. Add `--sys-dir` argument to `main()`. |
| **REC-03** | Engram | **HIGH** | `_sys/core/tidy_temp.py:40, 85-104, 211-220` | **Fix `_sys` renameability in tidy (Finding H-1):** Derive cleanup targets from `_SYS_DIR` rather than `ROOT / "_sys"`. |
| **REC-04** | PeerHub | **HIGH** | `peerhub/cli/__init__.py:1978` | **Remove hardcoded ancestor traversal:** In `peerhub routing import-capabilities`, do not assume `workspace_root.parent.parent / "_sys" / "ai"`. Require explicit `--protocol` and `--orchestration` paths, or fall back to packaged default configurations. |
| **REC-05** | PeerHub | **MED** | `peerhub/telemetry/quota_polling.py:180-184, 278, 629` | **Decouple quota polling from Engram internal layout:** Replace hardcoded `_sys/env/nodejs/npm-global` and `_sys/claude/config` with standard CLI discovery (via `shutil.which` or adapter configuration). Honor native `.engram` or user home config paths. |
| **REC-06** | Engram | **MED** | `_sys/core/provisioner.py:924-925` | **Fix Node.js false-positive in Codex liveness (Finding M-1):** Differentiate `codex.exe` from generic `node.exe` by checking command-line arguments (`proc.cmdline()`) rather than treating any Node process under `_sys` as Codex. |
| **REC-07** | Engram | **MED** | `_sys/checks/backup_personal_data.py:570` | **Reconcile backup CLI arg contract (Finding M-3):** Make `--out` optional in `main()`, defaulting to `sys_dir / "data" / "backups" / ...`, consistent with its docstring and `engram backup` adapter behavior. |
| **REC-08** | Engram | **LOW** | `README.md:8` | **Update test count badge (Finding L-2):** Update badge from `330 green` to `406 green` (or `403 passed, 3 skipped`) to match `pytest --collect-only`. |
| **REC-09** | Engram | **LOW** | `docs/engram-dotdir.md:9` | **Purge retired verb references (Finding M-4):** Replace `engram launch` with `engram` / `engram open`. Clarify in documentation that `PEERHUB_CONFIG_HOME` is an intentional compatibility bridge for portable isolation. |
| **REC-10** | Engram | **LOW** | `_sys/checks/check_encoding.py:58` | **Clean up dead exclusion (Finding L-3):** Remove `_sys/docs/history/` from exclusion set. |

---

## 8. Conclusion

The cross-repo MECE boundary audit is complete across all 5 assigned dimensions:
- The history migration from Engram to PeerHub is 100% verified byte-for-byte.
- Version reference hygiene across both repositories is confirmed clean.
- Independent verification confirms `cx.effort`'s findings (H-1, H-2, M-1, M-3, M-4, L-2, L-3).
- Critical unaddressed coupling was uncovered on the PeerHub side (`peerhub/cli/__init__.py:1978` and `quota_polling.py`), while Engram's boundary was clarified.
- The git worktree and filesystem architecture risks were cataloged with explicit hazard analysis.
