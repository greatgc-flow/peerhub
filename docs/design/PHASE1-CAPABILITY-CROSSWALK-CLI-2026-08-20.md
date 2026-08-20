# Phase 1: CLI Capability & Consumer Migration Crosswalk (`_sys/cli`)

> **DOCUMENT: Phase 1 Dialectical Revision (Round 3, Task 1 of 6)**  
> **AUTHOR:** `ag` (DeepMind Advanced Agentic Coding)  
> **SCOPE:** Exhaustive capability decomposition of all 39 legacy files in `_sys/cli`  
> **TARGET PATH:** `docs/design/PHASE1-CAPABILITY-CROSSWALK-CLI-2026-08-20.md`  
> **COMPLIANCE:** Addresses finding **R2-01** from cx's Round 2 counter-critique (`docs/design/PHASE1-CX-COUNTERCRITIQUE-ROUND2-2026-08-20.md`) & **DIR-004** (Measured-Only Claims).

---

## 1. Executive Summary & Namespace Disambiguation

In Round 2 critique finding **R2-01**, cx correctly established that the term *capability* had become severely overloaded across three conflicting domains:
1. **`migration_capability_id` (Migration / Architecture Domain):** Functional responsibility and ownership decomposition for legacy files and exported symbols during Phase 1–3 refactoring.
2. **`adapter_feature` (Runtime Contract Domain):** The strict runtime capability enum defined in `peerhub/adapters/contract.py`, strictly restricted to `SESSION`, `STREAM`, and `GRACEFUL_CANCEL`.
3. **`coverage_case_id` (Release Matrix Domain):** Exact release-proof and test matrix ledger rows defined in the test taxonomy.

This document provides the normative **`migration_capability_id`** crosswalk for all **39 files** in `_sys/cli`. For mixed-concern files, exported symbols and sub-capabilities are individually mapped.

### Reserved Fields Notation
- **`adapter_feature`**: *[Reserved — Unpopulated in migration crosswalk]* — Stays strictly `SESSION`, `STREAM`, `GRACEFUL_CANCEL` in `peerhub/adapters/contract.py`.
- **`coverage_case_id`**: *[Reserved — TBD by subsequent Phase 1 test matrix]*.

---

## 2. Exhaustive 39-File Verification & Summary Statistics

- **Total Legacy Files Covered:** 39 / 39 (100% MECE verified)
- **Total Crosswalk Capability Rows:** 71
- **Dispositions Breakdown:**
  - **`replace`**: 40 rows (Replaced by native PeerHub subsystems/adapters)
  - **`deprecate`**: 10 rows (Deprecated legacy shims/fallbacks)
  - **`stay`**: 15 rows (Preserved in Engram host toolchain)
  - **`split`**: 6 rows (Split between host toolchain and PeerHub core)

### 39 Files Checklist
| # | File Name | Kind | Disposition Summary | Row Count |
|---|---|---|---|---|
| 1 | `_bat-shim` | Generic Shim Bridge | `replace` | 1 |
| 2 | `ag_statusline.py` | Python Module | `replace` | 1 |
| 3 | `agy` | POSIX Bash Shim | `deprecate` | 1 |
| 4 | `agy.bat` | Windows Batch Wrapper | `replace` | 1 |
| 5 | `agy_entry.py` | Python Module | `replace` | 1 |
| 6 | `batch_review.py` | Python Module | `split/stay` | 4 |
| 7 | `batch-review` | POSIX Bash Shim | `stay` | 1 |
| 8 | `batch-review.bat` | Windows Batch Wrapper | `stay` | 1 |
| 9 | `claude` | POSIX Bash Shim | `deprecate` | 1 |
| 10 | `claude.bat` | Windows Batch Wrapper | `replace` | 1 |
| 11 | `claude_entry.py` | Python Module | `replace` | 1 |
| 12 | `cleanup.py` | Python Module | `split` | 2 |
| 13 | `codex` | POSIX Bash Shim | `deprecate` | 1 |
| 14 | `codex.bat` | Windows Batch Wrapper | `replace` | 1 |
| 15 | `codex_entry.py` | Python Module | `replace` | 1 |
| 16 | `collab-rate-gate` | POSIX Bash Shim | `deprecate` | 1 |
| 17 | `collab-rate-gate.bat` | Windows Batch Wrapper | `deprecate` | 1 |
| 18 | `console_runner.py` | Python Module | `replace` | 6 |
| 19 | `diag` | POSIX Bash Shim | `replace` | 1 |
| 20 | `diag.bat` | Windows Batch Wrapper | `replace` | 1 |
| 21 | `diag.py` | Python Module | `replace/split` | 10 |
| 22 | `git_draft.py` | Python Module | `stay` | 2 |
| 23 | `git-draft` | POSIX Bash Shim | `stay` | 1 |
| 24 | `git-draft.bat` | Windows Batch Wrapper | `stay` | 1 |
| 25 | `hub` | POSIX Bash Shim | `replace` | 1 |
| 26 | `hub.bat` | Windows Batch Wrapper | `replace` | 1 |
| 27 | `launch` | POSIX Bash Shim | `stay` | 1 |
| 28 | `launch.bat` | Windows Batch Wrapper | `stay` | 1 |
| 29 | `launcher.py` | Python Module | `deprecate` | 1 |
| 30 | `manage` | POSIX Bash Shim | `stay` | 1 |
| 31 | `manage.bat` | Windows Batch Wrapper | `stay` | 1 |
| 32 | `manage.py` | Python Module | `deprecate/split/stay` | 3 |
| 33 | `msg` | POSIX Bash Shim | `replace` | 1 |
| 34 | `msg.bat` | Windows Batch Wrapper | `replace` | 1 |
| 35 | `peer_console.py` | Python Module | `deprecate/replace` | 6 |
| 36 | `peer_mgr.py` | Python Module | `replace/split` | 7 |
| 37 | `peerhub.bat` | Windows Batch Wrapper | `stay` | 1 |
| 38 | `set-collab-rate` | POSIX Bash Shim | `deprecate` | 1 |
| 39 | `set-collab-rate.bat` | Windows Batch Wrapper | `deprecate` | 1 |

---

## 3. Migration Capability Crosswalk Ledger

### Row 1: `mig.cli.shim.posix_bash_bridge`
- **Legacy File / Symbol:** `_sys/cli/_bat-shim`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.cli.compat.shim / explicit shim lifecycle manager`
- **Current Real Consumers (Empirically Measured):** 12 POSIX bash wrapper scripts (_sys/cli/agy, batch-review, claude, codex, collab-rate-gate, diag, git-draft, hub, launch, manage, msg, set-collab-rate)
- **State Read / Written:** Reads BASH_SOURCE, caller path; writes no state.
- **External Effects:** Executes cmd.exe //d //q //c call <script_dir>/<command_name>.bat "$@" via bash subprocess.
- **Compatibility Actions / Fixtures:** Provision managed bash shims only during explicit 'peerhub compat install'; fixture_posix_shim_dispatch.
- **Retirement Condition:** Cutover of Git-Bash/MSYS2 environment to native POSIX entrypoints or direct peerhub CLI invocation.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 2: `mig.cli.shim.agy_posix`
- **Legacy File / Symbol:** `_sys/cli/agy`
- **Disposition:** `DEPRECATE`
- **Target Owner / API:** `peerhub.cli.compat / peerhub console ag`
- **Current Real Consumers (Empirically Measured):** Interactive Git-Bash users; referenced in _sys/ai/infra.json, _sys/ai/capability-declarations.json, _sys/ai/knowledge/peer-characteristics.jsonl
- **State Read / Written:** Reads BASH_SOURCE[0]; writes no state.
- **External Effects:** Sources _bat-shim to execute agy.bat.
- **Compatibility Actions / Fixtures:** Optional compatibility shim created during explicit shim installation; fixture_posix_shim_agy.
- **Retirement Condition:** Interactive console users cut over to 'peerhub console ag' or native PATH aliases.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 3: `mig.cli.shim.batch_review_posix`
- **Legacy File / Symbol:** `_sys/cli/batch-review`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host review toolchain (out of PeerHub core)`
- **Current Real Consumers (Empirically Measured):** Interactive Git-Bash users; _sys/docs-v2/user/manual.md, _sys/docs/history/SYSTEM_ARCHITECTURE_v3_legacy.md, Engram/docs/ARCHIVE-2026-08-19-workspace-scratch.md
- **State Read / Written:** Reads BASH_SOURCE[0]; writes no state.
- **External Effects:** Sources _bat-shim to execute batch-review.bat.
- **Compatibility Actions / Fixtures:** Preserved in Engram host toolchain root; excluded from PeerHub distribution.
- **Retirement Condition:** Engram transitions batch review to standalone host tool package.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 4: `mig.cli.shim.claude_posix`
- **Legacy File / Symbol:** `_sys/cli/claude`
- **Disposition:** `DEPRECATE`
- **Target Owner / API:** `peerhub.cli.compat / peerhub console cc`
- **Current Real Consumers (Empirically Measured):** Interactive Git-Bash users; _sys/ai/governance_params.json, _sys/ai/infra.json, _sys/ai/common/skills/health-check.md
- **State Read / Written:** Reads BASH_SOURCE[0]; writes no state.
- **External Effects:** Sources _bat-shim to execute claude.bat.
- **Compatibility Actions / Fixtures:** Managed compatibility shim created via 'peerhub compat install'; fixture_posix_shim_claude.
- **Retirement Condition:** Interactive console workflows migrate to 'peerhub console cc'.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 5: `mig.cli.shim.codex_posix`
- **Legacy File / Symbol:** `_sys/cli/codex`
- **Disposition:** `DEPRECATE`
- **Target Owner / API:** `peerhub.cli.compat / peerhub console cx`
- **Current Real Consumers (Empirically Measured):** Interactive Git-Bash users; _sys/ai/model-registry.json, _sys/ai/orchestration.json, _sys/ai/infra.json, _sys/ai/common/skills/health-check.md
- **State Read / Written:** Reads BASH_SOURCE[0]; writes no state.
- **External Effects:** Sources _bat-shim to execute codex.bat.
- **Compatibility Actions / Fixtures:** Managed compatibility shim created via 'peerhub compat install'; fixture_posix_shim_codex.
- **Retirement Condition:** Interactive console workflows migrate to 'peerhub console cx'.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 6: `mig.cli.shim.collab_rate_gate_posix`
- **Legacy File / Symbol:** `_sys/cli/collab-rate-gate`
- **Disposition:** `DEPRECATE`
- **Target Owner / API:** `peerhub-engram bridge / Engram host governance`
- **Current Real Consumers (Empirically Measured):** Git hooks / bash scripts checking collaboration threshold; _sys/ai/infra.json, Engram/docs/ARCHIVE-2026-08-19-workspace-scratch.md
- **State Read / Written:** Reads BASH_SOURCE[0]; writes no state.
- **External Effects:** Sources _bat-shim to execute collab-rate-gate.bat.
- **Compatibility Actions / Fixtures:** Expose collaboration threshold evaluation via 'peerhub policy check --collab-rate'.
- **Retirement Condition:** Git hooks updated to query PeerHub policy API directly.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 7: `mig.cli.shim.diag_posix`
- **Legacy File / Symbol:** `_sys/cli/diag`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.cli (peerhub diag)`
- **Current Real Consumers (Empirically Measured):** Terminal operators running diag; _sys/ai/common/statusline/statusline-unified.sh, _sys/ai/infra.json, _sys/ai/status_checks.json
- **State Read / Written:** Reads BASH_SOURCE[0]; writes no state.
- **External Effects:** Sources _bat-shim to execute diag.bat.
- **Compatibility Actions / Fixtures:** Optional bash shim forwarding to 'peerhub diag'; fixture_diag_posix_shim.
- **Retirement Condition:** Scripts and operators invoke 'peerhub diag' directly.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 8: `mig.cli.shim.git_draft_posix`
- **Legacy File / Symbol:** `_sys/cli/git-draft`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host developer tooling`
- **Current Real Consumers (Empirically Measured):** Developers running git-draft; _sys/docs-v2/ops/conventions.md, _sys/docs-v2/user/manual.md, _sys/tests/local-test.bat
- **State Read / Written:** Reads BASH_SOURCE[0]; writes no state.
- **External Effects:** Sources _bat-shim to execute git-draft.bat.
- **Compatibility Actions / Fixtures:** Preserved in Engram host repository; excluded from PeerHub core package.
- **Retirement Condition:** Engram host packages git utilities independently.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 9: `mig.cli.shim.hub_posix`
- **Legacy File / Symbol:** `_sys/cli/hub`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.cli (peerhub)`
- **Current Real Consumers (Empirically Measured):** Subagent skills and bash orchestration; _sys/ai/common/skills/* (consensus-vote, context-fill, health-check, lesson-add, peer-propose), _sys/ai/orchestration.json
- **State Read / Written:** Reads BASH_SOURCE[0]; writes no state.
- **External Effects:** Sources _bat-shim to execute hub.bat.
- **Compatibility Actions / Fixtures:** Managed compatibility shim forwarding to 'peerhub'; fixture_hub_posix_compat.
- **Retirement Condition:** All subagent skills and orchestration workflows point to 'peerhub'.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 10: `mig.cli.shim.launch_posix`
- **Legacy File / Symbol:** `_sys/cli/launch`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host portable launcher`
- **Current Real Consumers (Empirically Measured):** Terminal operators; _sys/ai/infra.json, _sys/ai/peers.json, _sys/ai/orchestration.json
- **State Read / Written:** Reads BASH_SOURCE[0]; writes no state.
- **External Effects:** Sources _bat-shim to execute launch.bat -> _sys/start.bat.
- **Compatibility Actions / Fixtures:** Preserved in Engram host portable root.
- **Retirement Condition:** Host environment modernization.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 11: `mig.cli.shim.manage_posix`
- **Legacy File / Symbol:** `_sys/cli/manage`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host environment manager`
- **Current Real Consumers (Empirically Measured):** Terminal operators; _sys/ai/infra.json, _sys/checks/check_cli_reality.py, _sys/checks/check_deps.py
- **State Read / Written:** Reads BASH_SOURCE[0]; writes no state.
- **External Effects:** Sources _bat-shim to execute manage.bat.
- **Compatibility Actions / Fixtures:** Preserved in Engram host portable root.
- **Retirement Condition:** Host environment manager modernization.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 12: `mig.cli.shim.msg_posix`
- **Legacy File / Symbol:** `_sys/cli/msg`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.cli (peerhub ask / peerhub send)`
- **Current Real Consumers (Empirically Measured):** Peer scripts and collaboration loops; _sys/ai/collaboration_loop_bindings.json, _sys/ai/peers.json, _sys/ai/protocol.json, _sys/ai/room_policy.example.json
- **State Read / Written:** Reads BASH_SOURCE[0]; writes no state.
- **External Effects:** Sources _bat-shim to execute msg.bat.
- **Compatibility Actions / Fixtures:** Managed compatibility shim translating 'msg' syntax to 'peerhub ask'/'peerhub send'.
- **Retirement Condition:** All peer collaboration scripts updated to PeerHub CLI.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 13: `mig.cli.shim.set_collab_rate_posix`
- **Legacy File / Symbol:** `_sys/cli/set-collab-rate`
- **Disposition:** `DEPRECATE`
- **Target Owner / API:** `peerhub-engram bridge / Engram policy manager`
- **Current Real Consumers (Empirically Measured):** Terminal operators; _sys/ai/infra.json, _sys/docs-v2/user/manual.md, Engram/docs/ARCHIVE-2026-08-19-workspace-scratch.md
- **State Read / Written:** Reads BASH_SOURCE[0]; writes no state.
- **External Effects:** Sources _bat-shim to execute set-collab-rate.bat.
- **Compatibility Actions / Fixtures:** Replace with 'peerhub policy set-collab-rate <N>'.
- **Retirement Condition:** Collaboration rate governance managed through PeerHub policy engine.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 14: `mig.cli.wrapper.agy_bat`
- **Legacy File / Symbol:** `_sys/cli/agy.bat`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.cli.compat / peerhub console ag`
- **Current Real Consumers (Empirically Measured):** Windows console users; _sys/cli/agy, _sys/ai/infra.json, _sys/ai/orchestration.json, _sys/ai/peers.json, _sys/docs-v2/specific/ag.md
- **State Read / Written:** Reads _sys/env/venv/Scripts/python.exe, _sys/cli/agy_entry.py; sets PYTHONUTF8=1.
- **External Effects:** Spawns python.exe running agy_entry.py %*.
- **Compatibility Actions / Fixtures:** Controlled batch shim generated during 'peerhub compat install'; fixture_windows_wrapper_agy.
- **Retirement Condition:** Users and shortcuts adopt 'peerhub console ag'.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 15: `mig.cli.wrapper.batch_review_bat`
- **Legacy File / Symbol:** `_sys/cli/batch-review.bat`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host review toolchain`
- **Current Real Consumers (Empirically Measured):** Windows operators / hook callers; _sys/cli/batch-review, Engram/docs/ARCHIVE-2026-08-19-workspace-scratch.md
- **State Read / Written:** Reads _sys/env/venv/Scripts/python.exe, _sys/cli/batch_review.py; sets PYTHONUTF8=1.
- **External Effects:** Spawns python.exe running batch_review.py %*.
- **Compatibility Actions / Fixtures:** Preserved in Engram host toolchain.
- **Retirement Condition:** Engram transitions batch review to independent host tool.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 16: `mig.cli.wrapper.claude_bat`
- **Legacy File / Symbol:** `_sys/cli/claude.bat`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.cli.compat / peerhub console cc`
- **Current Real Consumers (Empirically Measured):** Windows console users; _sys/cli/claude, _sys/ai/infra.json, _sys/ai/orchestration.json, _sys/ai/peers.json, _sys/cli/peer_console.py, _sys/docs-v2/user/manual.md
- **State Read / Written:** Reads _sys/env/venv/Scripts/python.exe, _sys/cli/claude_entry.py; sets PYTHONUTF8=1.
- **External Effects:** Spawns python.exe running claude_entry.py %*.
- **Compatibility Actions / Fixtures:** Controlled batch shim generated during 'peerhub compat install'; fixture_windows_wrapper_claude.
- **Retirement Condition:** Users and shortcuts adopt 'peerhub console cc'.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 17: `mig.cli.wrapper.codex_bat`
- **Legacy File / Symbol:** `_sys/cli/codex.bat`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.cli.compat / peerhub console cx`
- **Current Real Consumers (Empirically Measured):** Windows console users; _sys/cli/codex, _sys/ai/infra.json, _sys/ai/orchestration.json, _sys/ai/peers.json, _sys/core/snapshot.py, _sys/docs-v2/specific/cx.md
- **State Read / Written:** Reads _sys/env/venv/Scripts/python.exe, _sys/cli/codex_entry.py; sets PYTHONUTF8=1.
- **External Effects:** Spawns python.exe running codex_entry.py %*.
- **Compatibility Actions / Fixtures:** Controlled batch shim generated during 'peerhub compat install'; fixture_windows_wrapper_codex.
- **Retirement Condition:** Users and shortcuts adopt 'peerhub console cx'.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 18: `mig.cli.wrapper.collab_rate_gate_bat`
- **Legacy File / Symbol:** `_sys/cli/collab-rate-gate.bat`
- **Disposition:** `DEPRECATE`
- **Target Owner / API:** `peerhub-engram bridge / Engram git hooks`
- **Current Real Consumers (Empirically Measured):** Git pre-commit/stop hooks; _sys/cli/collab-rate-gate, _sys/ai/infra.json, Engram/docs/ARCHIVE-2026-08-19-workspace-scratch.md
- **State Read / Written:** Reads _sysi\protocol.json via PowerShell.
- **External Effects:** Exits 0 if collab_rate >= THRESHOLD, else 1.
- **Compatibility Actions / Fixtures:** Replaced by native Python policy evaluator 'peerhub policy check --collab-rate'.
- **Retirement Condition:** Git hooks migrated to 'peerhub policy check'.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 19: `mig.cli.wrapper.diag_bat`
- **Legacy File / Symbol:** `_sys/cli/diag.bat`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.cli (peerhub diag)`
- **Current Real Consumers (Empirically Measured):** Terminal operators; _sys/cli/diag, _sys/docs-v2/ops/logging.md, _sys/docs/history/ops/diag-redesign-design.md, Engram/README.md
- **State Read / Written:** Reads %~dp0peerhub.bat.
- **External Effects:** Invokes peerhub.bat diag %*.
- **Compatibility Actions / Fixtures:** Direct batch wrapper delegating to 'peerhub.bat diag %*'.
- **Retirement Condition:** Operators invoke 'peerhub diag' directly.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 20: `mig.cli.wrapper.git_draft_bat`
- **Legacy File / Symbol:** `_sys/cli/git-draft.bat`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host git utilities`
- **Current Real Consumers (Empirically Measured):** Windows developers; _sys/cli/git-draft, _sys/docs-v2/ops/conventions.md, _sys/tests/local-test.bat
- **State Read / Written:** Reads _sys/env/venv/Scripts/python.exe, _sys/cli/git_draft.py; sets PYTHONUTF8=1.
- **External Effects:** Spawns python.exe running git_draft.py %*.
- **Compatibility Actions / Fixtures:** Preserved in Engram host repository.
- **Retirement Condition:** Host developer git tooling refactored.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 21: `mig.cli.wrapper.hub_bat`
- **Legacy File / Symbol:** `_sys/cli/hub.bat`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.cli`
- **Current Real Consumers (Empirically Measured):** CLI operators and automated checks; _sys/cli/hub, _sys/ai/infra.json, _sys/tests/unit/test_check_cli_reality.py, Engram/README.md
- **State Read / Written:** Reads %~dp0peerhub.bat.
- **External Effects:** Invokes peerhub.bat %* (or peerhub.bat status if empty args).
- **Compatibility Actions / Fixtures:** Batch shim delegating to peerhub.bat; fixture_hub_bat_compat.
- **Retirement Condition:** All tool invocations use 'peerhub.bat' or 'peerhub'.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 22: `mig.cli.wrapper.launch_bat`
- **Legacy File / Symbol:** `_sys/cli/launch.bat`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host portable launcher`
- **Current Real Consumers (Empirically Measured):** Windows operators; _sys/cli/launch, _sys/checks/check_deps.py, _sys/docs-v2/ops/conventions.md, _sys/tests/unit/test_launcher_paths.py
- **State Read / Written:** Reads _sys/start.bat.
- **External Effects:** Calls _sys/start.bat %* with error handling pause.
- **Compatibility Actions / Fixtures:** Preserved in Engram host portable root.
- **Retirement Condition:** Host environment launcher modernization.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 23: `mig.cli.wrapper.manage_bat`
- **Legacy File / Symbol:** `_sys/cli/manage.bat`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host environment manager`
- **Current Real Consumers (Empirically Measured):** Windows operators; _sys/cli/manage, _sys/checks/check_deps.py, _sys/docs-v2/ops/audit-checklist.md, _sys/docs/history/CONVENTION.md
- **State Read / Written:** Reads _sys/env/python/python.exe, _sys/cli/manage.py.
- **External Effects:** Calls %PY% manage.py %*.
- **Compatibility Actions / Fixtures:** Preserved in Engram host portable root.
- **Retirement Condition:** Host environment manager modernization.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 24: `mig.cli.wrapper.msg_bat`
- **Legacy File / Symbol:** `_sys/cli/msg.bat`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.cli (peerhub ask / peerhub send / peerhub mailbox)`
- **Current Real Consumers (Empirically Measured):** Legacy peer IPC; _sys/cli/msg, _sys/ai/infra.json, _sys/ai/peers.json, _sys/ai/protocol.json, _sys/checks/check_deps.py
- **State Read / Written:** Sets PYTHONUTF8=1, adds venv/npm to PATH; executes _sys/core/hub.py %*.
- **External Effects:** Spawns python.exe running hub.py actions (ask, send, check, status).
- **Compatibility Actions / Fixtures:** Compatibility parser translating legacy 'msg' command line flags to 'peerhub ask'/'peerhub send'.
- **Retirement Condition:** All legacy scripts and skills migrated to PeerHub CLI.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 25: `mig.cli.wrapper.peerhub_bat`
- **Legacy File / Symbol:** `_sys/cli/peerhub.bat`
- **Disposition:** `STAY`
- **Target Owner / API:** `peerhub.cli (canonical Windows launcher)`
- **Current Real Consumers (Empirically Measured):** Windows operators and wrapper scripts (_sys/cli/diag.bat, _sys/cli/hub.bat, Engram/README.md)
- **State Read / Written:** Probes PORTABLE_ROOT/_sys/env/venv/Scripts/python.exe; configures PATH with venv, nodejs, npm-global, git.
- **External Effects:** Executes %PYTHON_EXE% -m peerhub.cli %*.
- **Compatibility Actions / Fixtures:** Standard Windows bootstrap launcher fixture verifying PATH setup and exit code propagation.
- **Retirement Condition:** Permanent canonical Windows launcher for portable installations.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 26: `mig.cli.wrapper.set_collab_rate_bat`
- **Legacy File / Symbol:** `_sys/cli/set-collab-rate.bat`
- **Disposition:** `DEPRECATE`
- **Target Owner / API:** `peerhub-engram bridge / Engram policy tool`
- **Current Real Consumers (Empirically Measured):** Windows operators; _sys/cli/set-collab-rate, _sys/ai/infra.json, _sys/docs-v2/user/manual.md
- **State Read / Written:** Reads and writes _sysi\protocol.json (collab_rate.current, active_constraints.current_collab_rate).
- **External Effects:** Overwrites _sysi\protocol.json via PowerShell ConvertFrom-Json / ConvertTo-Json.
- **Compatibility Actions / Fixtures:** Replace with atomic Python-based 'peerhub policy set-collab-rate <N>'.
- **Retirement Condition:** Protocol policy configuration unified in PeerHub governance store.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 27: `mig.cli.entry.agy`
- **Legacy File / Symbol:** `_sys/cli/agy_entry.py`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.cli.console / peerhub.adapters.agy`
- **Current Real Consumers (Empirically Measured):** _sys/cli/agy.bat, _sys/docs-v2/specific/ag.md, _sys/docs-v2/ops/architecture-audit-2026-07-24.md, _sys/tests/unit/test_console_runner_s3.py
- **State Read / Written:** Reads _sys/ai/peers.json (antigravity env_vars); calls hub.py init-session, health-update, context-fill.
- **External Effects:** Sets Windows console title; invokes console_runner.run_console_session() to spawn agy.exe.
- **Compatibility Actions / Fixtures:** Emulate pre-launch session init and health check in 'peerhub console ag'; fixture_console_launch_agy.
- **Retirement Condition:** Console launches handled natively through PeerHub CLI.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 28: `mig.cli.entry.claude`
- **Legacy File / Symbol:** `_sys/cli/claude_entry.py`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.cli.console / peerhub.adapters.claude`
- **Current Real Consumers (Empirically Measured):** _sys/cli/claude.bat, _sys/docs-v2/ops/backlog-design-consensus-2026-07-24.md, _sys/tests/unit/test_console_runner_s3.py
- **State Read / Written:** Reads _sys/ai/peers.json (claude env_vars); calls hub.py init-session, status.
- **External Effects:** Sets Windows console title; invokes console_runner.run_console_session() to spawn claude.cmd.
- **Compatibility Actions / Fixtures:** Emulate pre-launch session init in 'peerhub console cc'; fixture_console_launch_claude.
- **Retirement Condition:** Console launches handled natively through PeerHub CLI.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 29: `mig.cli.entry.codex`
- **Legacy File / Symbol:** `_sys/cli/codex_entry.py`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.cli.console / peerhub.adapters.codex`
- **Current Real Consumers (Empirically Measured):** _sys/cli/codex.bat, _sys/core/snapshot.py, _sys/docs-v2/specific/cx.md, _sys/docs-v2/ops/architecture-audit-2026-07-24.md, _sys/tests/unit/test_console_runner_s3.py
- **State Read / Written:** Reads _sys/ai/peers.json (codex env_vars); calls hub.py init-session, health-update, context-fill.
- **External Effects:** Sets Windows console title; invokes console_runner.run_console_session() to spawn codex.cmd.
- **Compatibility Actions / Fixtures:** Emulate pre-launch session init in 'peerhub console cx'; fixture_console_launch_codex.
- **Retirement Condition:** Console launches handled natively through PeerHub CLI.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 30: `mig.cli.util.ag_statusline`
- **Legacy File / Symbol:** `_sys/cli/ag_statusline.py`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.telemetry.statusline / peerhub.adapters.agy`
- **Current Real Consumers (Empirically Measured):** _sys/tests/unit/test_t12_t13_misc.py, _sys/docs/history/specific/statusline_diag_update.md, _sys/ai/backlog.json
- **State Read / Written:** Reads stdin JSON lines from Antigravity statusline protocol; writes formatted status to _sys/cli/.ai/statusline/ag.json or .peerhub/statusline/ag.json.
- **External Effects:** Appends raw telemetry frames to statusline log file.
- **Compatibility Actions / Fixtures:** Replace ad-hoc script with typed statusline reader daemon in PeerHub telemetry service; fixture_ag_statusline_stdin.
- **Retirement Condition:** Antigravity statusline telemetry ingested through PeerHub statusline subsystem.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 31: `mig.cli.util.cleanup_shim`
- **Legacy File / Symbol:** `_sys/cli/cleanup.py`
- **Disposition:** `SPLIT`
- **Target Owner / API:** `Engram host scrubber / peerhub.storage.cleanup`
- **Current Real Consumers (Empirically Measured):** _sys/cli/manage.py, _sys/core/dispatcher.py, _sys/checks/check_deps.py, _sys/checks/self_care.py
- **State Read / Written:** Reads base directory path; delegates to core.scrubber.
- **External Effects:** Calls core.scrubber.run_cleanup() (removes temp files, cleans logs, resets stale locks).
- **Compatibility Actions / Fixtures:** Legacy shim module forwarding run_cleanup() to core.scrubber; PeerHub state scrubbing handled by 'peerhub cleanup'.
- **Retirement Condition:** All callers invoke 'peerhub cleanup' or Engram host maintenance scripts directly.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 32: `mig.cli.util.cleanup_run_cleanup`
- **Legacy File / Symbol:** `_sys/cli/cleanup.py:run_cleanup`
- **Disposition:** `SPLIT`
- **Target Owner / API:** `core.scrubber (Engram host) / peerhub.storage.cleanup`
- **Current Real Consumers (Empirically Measured):** _sys/cli/manage.py, _sys/checks/check_deps.py
- **State Read / Written:** Accepts tier, all_yes, dry_run, base_dir; delegates to core.scrubber.
- **External Effects:** Executes scrubbing passes against filesystem.
- **Compatibility Actions / Fixtures:** Function signature preserved in legacy shim during transition.
- **Retirement Condition:** Callers migrated to core.scrubber or 'peerhub cleanup'.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 33: `mig.cli.util.git_draft`
- **Legacy File / Symbol:** `_sys/cli/git_draft.py`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host developer tooling (out of PeerHub core)`
- **Current Real Consumers (Empirically Measured):** _sys/cli/git-draft.bat, _sys/cli/batch_review.py (imports _get_diff), _sys/docs-v2/ops/conventions.md
- **State Read / Written:** Reads git working tree diff via 'git diff'; writes temporary draft prompt file.
- **External Effects:** Spawns git diff; invokes Gemini AI API (_common.gemini_call); prints conventional commit message draft.
- **Compatibility Actions / Fixtures:** Preserved in Engram tools directory.
- **Retirement Condition:** Engram packages developer git utilities independently.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 34: `mig.cli.util.git_draft_get_diff`
- **Legacy File / Symbol:** `_sys/cli/git_draft.py:_get_diff`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host developer tooling`
- **Current Real Consumers (Empirically Measured):** _sys/cli/batch_review.py
- **State Read / Written:** Runs 'git diff --cached' or 'git diff HEAD' in root directory.
- **External Effects:** Spawns git process and captures stdout diff string.
- **Compatibility Actions / Fixtures:** Preserved in Engram helper library.
- **Retirement Condition:** Engram review utilities refactored.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 35: `mig.cli.util.launcher_shim`
- **Legacy File / Symbol:** `_sys/cli/launcher.py`
- **Disposition:** `DEPRECATE`
- **Target Owner / API:** `core.launcher (Engram host launcher)`
- **Current Real Consumers (Empirically Measured):** _sys/checks/check_cli_reality.py, _sys/core/relocator.py, _sys/core/scrubber.py, _sys/dispatch.json
- **State Read / Written:** Imports core.launcher; reads base directory.
- **External Effects:** Delegates execution to core.launcher.main().
- **Compatibility Actions / Fixtures:** Thin forwarding wrapper kept for legacy PATH imports.
- **Retirement Condition:** All dispatchers and tests import core.launcher directly.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 36: `mig.cli.review.batch_review_main`
- **Legacy File / Symbol:** `_sys/cli/batch_review.py`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host review toolchain (out of PeerHub core)`
- **Current Real Consumers (Empirically Measured):** _sys/cli/batch-review.bat, _sys/tests/unit/test_t12_t13_misc.py, _sys/docs/history/ops/hardcoding-full-audit-2026-07-09.md
- **State Read / Written:** Reads _sys/ai/protocol.json, _sys/ai/batch_review_state.json; writes review state and archive markdown to _archive/gemini-reviews/.
- **External Effects:** Spawns git diff; invokes Gemini AI API; writes markdown review reports to disk.
- **Compatibility Actions / Fixtures:** Preserved in Engram host repository.
- **Retirement Condition:** Engram review system refactored to host plugin.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 37: `mig.cli.review.policy_loader`
- **Legacy File / Symbol:** `_sys/cli/batch_review.py:_load_collab_policy`
- **Disposition:** `SPLIT`
- **Target Owner / API:** `peerhub-engram bridge / Engram policy manager`
- **Current Real Consumers (Empirically Measured):** _sys/cli/batch_review.py internal
- **State Read / Written:** Reads _sys/ai/protocol.json (collab_rate.current, batch_review settings).
- **External Effects:** None (pure JSON load).
- **Compatibility Actions / Fixtures:** Bridge adapter providing typed PolicySnapshot from Engram protocol.json.
- **Retirement Condition:** Engram policy engine modernization.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 38: `mig.cli.review.time_gate`
- **Legacy File / Symbol:** `_sys/cli/batch_review.py:_time_gate_ok`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host review toolchain`
- **Current Real Consumers (Empirically Measured):** _sys/cli/batch_review.py internal
- **State Read / Written:** Reads _sys/ai/batch_review_state.json timestamp.
- **External Effects:** Evaluates whether minimum review interval has elapsed.
- **Compatibility Actions / Fixtures:** Preserved in Engram host review helper.
- **Retirement Condition:** Engram review agent migration.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 39: `mig.cli.review.git_diff_extractor`
- **Legacy File / Symbol:** `_sys/cli/batch_review.py:_get_diff`
- **Disposition:** `STAY`
- **Target Owner / API:** `Engram host review toolchain`
- **Current Real Consumers (Empirically Measured):** _sys/cli/batch_review.py internal
- **State Read / Written:** Reads git status and diff across root directory.
- **External Effects:** Spawns git diff subprocess.
- **Compatibility Actions / Fixtures:** Preserved in Engram host review helper.
- **Retirement Condition:** Engram review agent migration.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 40: `mig.cli.manage.manage_main`
- **Legacy File / Symbol:** `_sys/cli/manage.py`
- **Disposition:** `SPLIT`
- **Target Owner / API:** `Engram host environment manager (core.virtualizer, core.registrar)`
- **Current Real Consumers (Empirically Measured):** _sys/cli/manage.bat, _sys/checks/check_deps.py, _sys/core/hub.py, _sys/docs-v2/ops/audit-checklist.md
- **State Read / Written:** Reads _sys/paths.json, _sys/ai/peers.json, _sys/config.json; delegates mounting and registration to core.virtualizer and core.registrar.
- **External Effects:** Spawns subst.exe / junction.exe; sets up virtual drive mounts and environment registrations.
- **Compatibility Actions / Fixtures:** Preserved in Engram host repository for portable environment management.
- **Retirement Condition:** Engram portable runner refactored.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 41: `mig.cli.manage.subst_mappings`
- **Legacy File / Symbol:** `_sys/cli/manage.py:get_subst_mappings`
- **Disposition:** `STAY`
- **Target Owner / API:** `core.virtualizer (Engram host)`
- **Current Real Consumers (Empirically Measured):** _sys/tests/unit/test_launcher_paths.py, _sys/ai/unreferenced_functions_baseline.json
- **State Read / Written:** Executes subst command without arguments; parses drive letter to physical path mapping.
- **External Effects:** Queries Windows subst virtual drive table.
- **Compatibility Actions / Fixtures:** Exported from core.virtualizer; fixture_subst_parse.
- **Retirement Condition:** Host virtualizer modernization.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 42: `mig.cli.manage.workspace_init_legacy`
- **Legacy File / Symbol:** `_sys/cli/manage.py:_workspace_init_legacy`
- **Disposition:** `DEPRECATE`
- **Target Owner / API:** `Engram host workspace provisioner`
- **Current Real Consumers (Empirically Measured):** Legacy manage.py fallback
- **State Read / Written:** Creates .ai directory junction and glue files in target workspace.
- **External Effects:** Modifies workspace directory structure on disk.
- **Compatibility Actions / Fixtures:** Replaced by PeerHub native PathLayout creating .peerhub metadata roots.
- **Retirement Condition:** All workspaces operate on .peerhub directory without .ai junctions.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 43: `mig.cli.runner.run_console_session`
- **Legacy File / Symbol:** `_sys/cli/console_runner.py`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.cli.console / peerhub.engine.interactive_runner`
- **Current Real Consumers (Empirically Measured):** _sys/cli/agy_entry.py, _sys/cli/claude_entry.py, _sys/cli/codex_entry.py, _sys/tests/unit/test_console_runner_s3.py, _sys/docs-v2/ops/backlog-design-consensus-2026-07-24.md
- **State Read / Written:** Reads ConsoleSessionSpec, peer_id, argv; claims terminal lease; spawns interactive process; updates peer health.
- **External Effects:** Launches interactive child process with inherited stdio; spawns heartbeat thread; invokes hub terminal-handoff / terminal-heartbeat.
- **Compatibility Actions / Fixtures:** Refactor to consume PeerHub adapter manifests and native lease manager; fixture_console_runner_lifecycle.
- **Retirement Condition:** All peer console entries use 'peerhub console <peer>'.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 44: `mig.cli.runner.lease_duty_classifier`
- **Legacy File / Symbol:** `_sys/cli/console_runner.py:should_claim_lease`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.coordination.lease_policy`
- **Current Real Consumers (Empirically Measured):** _sys/tests/unit/test_console_runner_s3.py
- **State Read / Written:** Pure classification function mapping InvocationKind to bool (AGENT/CHAT/RESUME -> True, EXEC/ADMIN/PRINT -> False).
- **External Effects:** None (pure mapping).
- **Compatibility Actions / Fixtures:** Integrated into PeerHub coordination policy engine; fixture_lease_duty_mapping.
- **Retirement Condition:** PeerHub lease engine owns terminal duty decisions.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 45: `mig.cli.runner.terminal_lease_client`
- **Legacy File / Symbol:** `_sys/cli/console_runner.py:_claim_terminal_lease`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.coordination.lease_manager`
- **Current Real Consumers (Empirically Measured):** _sys/tests/unit/test_console_runner_s3.py
- **State Read / Written:** Executes 'hub.py terminal-handoff --claim' via subprocess.
- **External Effects:** Registers terminal lease ownership in hub lease registry.
- **Compatibility Actions / Fixtures:** Direct Python API call to PeerHub lease manager replacing hub.py CLI subprocess invocation.
- **Retirement Condition:** hub.py terminal-handoff retired.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 46: `mig.cli.runner.heartbeat_renew`
- **Legacy File / Symbol:** `_sys/cli/console_runner.py:_renew_heartbeat`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.coordination.lease_manager`
- **Current Real Consumers (Empirically Measured):** _sys/tests/unit/test_console_runner_s3.py
- **State Read / Written:** Executes 'hub.py terminal-heartbeat' via subprocess.
- **External Effects:** Renews lease expiration timestamp in lease store.
- **Compatibility Actions / Fixtures:** Integrated background heartbeat thread in PeerHub interactive runner.
- **Retirement Condition:** hub.py terminal-heartbeat retired.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 47: `mig.cli.runner.health_update`
- **Legacy File / Symbol:** `_sys/cli/console_runner.py:_update_peer_health_json`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.health.manager`
- **Current Real Consumers (Empirically Measured):** _sys/tests/unit/test_console_runner_s3.py
- **State Read / Written:** Updates _sys/ai/health.json (or calls hub.py health-update).
- **External Effects:** Modifies health telemetry file on disk.
- **Compatibility Actions / Fixtures:** Typed health record publication in PeerHub health store.
- **Retirement Condition:** _sys/ai/health.json format migrated to .peerhub/health.json.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 48: `mig.cli.runner.types`
- **Legacy File / Symbol:** `_sys/cli/console_runner.py:ConsoleSessionSpec`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.types.console`
- **Current Real Consumers (Empirically Measured):** _sys/tests/unit/test_console_runner_s3.py, _sys/cli/agy_entry.py, _sys/cli/claude_entry.py, _sys/cli/codex_entry.py
- **State Read / Written:** Dataclasses defining console session parameters (ConsoleSessionSpec) and outcomes (ConsoleResult).
- **External Effects:** None (type definitions).
- **Compatibility Actions / Fixtures:** Migrated to typed Pydantic models / dataclasses in peerhub.types.
- **Retirement Condition:** Complete migration to PeerHub console execution engine.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 49: `mig.cli.console.prepare_console_launch`
- **Legacy File / Symbol:** `_sys/cli/peer_console.py:prepare_console_launch`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.adapters.base.ConsoleClassifier / peerhub.cli.console`
- **Current Real Consumers (Empirically Measured):** _sys/cli/console_runner.py, _sys/tests/unit/test_peer_console_c8b.py, _sys/docs-v2/ops/backlog-design-consensus-2026-07-24.md
- **State Read / Written:** Reads _sys/ai/orchestration.json (profile defaults, forbidden args); classifies invocation; builds final argv.
- **External Effects:** Returns immutable ConsoleLaunch or raises SecurityValidationError.
- **Compatibility Actions / Fixtures:** Migrate test_peer_console_c8b.py test cases to tests/unit/test_adapter_console_launch.py.
- **Retirement Condition:** PeerHub adapter manifests and CLI console runner own argument defaults.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 50: `mig.cli.console.peer_default_args`
- **Legacy File / Symbol:** `_sys/cli/peer_console.py:peer_default_args`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.adapters.contract`
- **Current Real Consumers (Empirically Measured):** _sys/core/hub.py, _sys/tests/unit/test_peer_console_c8a.py, _sys/tests/unit/test_permission_matrix.py, _sys/docs-v2/ops/architecture-audit-2026-07-24.md
- **State Read / Written:** Wrapper calling prepare_console_launch and returning final_argv.
- **External Effects:** None.
- **Compatibility Actions / Fixtures:** Legacy helper shim forwarding to adapter invocation planner during shadow run.
- **Retirement Condition:** hub.py callers cut over to adapter.plan_invocation().
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 51: `mig.cli.console.apply_security_semantics`
- **Legacy File / Symbol:** `_sys/cli/peer_console.py:apply_security_semantics`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.adapters.security_translator`
- **Current Real Consumers (Empirically Measured):** _sys/checks/check_cli_reality.py, _sys/tests/unit/test_check_cli_reality.py, _sys/docs-v2/ops/architecture-audit-2026-07-24.md
- **State Read / Written:** Translates security contract into CLI permission flags (--dangerously-skip-permissions, -s workspace-write).
- **External Effects:** Modifies command argv array.
- **Compatibility Actions / Fixtures:** Move policy translation to adapter-specific security mappings; fixture_security_semantics.
- **Retirement Condition:** check_cli_reality and hub.py use PeerHub adapter security layer.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 52: `mig.cli.console.security_validation_error`
- **Legacy File / Symbol:** `_sys/cli/peer_console.py:SecurityValidationError`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.errors.SecurityValidationError`
- **Current Real Consumers (Empirically Measured):** _sys/cli/console_runner.py, _sys/tests/unit/test_console_runner_s3.py, _sys/tests/unit/test_peer_console_c8b.py
- **State Read / Written:** Exception raised on forbidden CLI security flags without break-glass permission.
- **External Effects:** Aborts console launch with security diagnostic.
- **Compatibility Actions / Fixtures:** Standard typed exception in peerhub.errors.
- **Retirement Condition:** console_runner replaced by PeerHub CLI.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 53: `mig.cli.console.types`
- **Legacy File / Symbol:** `_sys/cli/peer_console.py:InvocationKind`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.types.invocation`
- **Current Real Consumers (Empirically Measured):** _sys/cli/console_runner.py, _sys/tests/unit/test_console_runner_s3.py, _sys/tests/unit/test_peer_console_c8b.py, docs/design/peerhub-architecture-debate.md
- **State Read / Written:** Enum InvocationKind (AGENT, CHAT, RESUME, EXEC, PRINT, ADMIN, SUBCOMMAND) and dataclass ConsoleLaunch.
- **External Effects:** None (type definitions).
- **Compatibility Actions / Fixtures:** Migrated to peerhub.types.invocation.
- **Retirement Condition:** All console callers use peerhub.types.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 54: `mig.cli.console.interactive_profile_banner`
- **Legacy File / Symbol:** `_sys/cli/peer_console.py:interactive_profile_banner`
- **Disposition:** `DEPRECATE`
- **Target Owner / API:** `peerhub.cli.ui`
- **Current Real Consumers (Empirically Measured):** _sys/ai/unreferenced_functions_baseline.json
- **State Read / Written:** Generates formatted interactive console startup banner.
- **External Effects:** Returns formatted string.
- **Compatibility Actions / Fixtures:** Replaced by PeerHub CLI console banner renderer.
- **Retirement Condition:** Legacy entrypoints retired.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 55: `mig.cli.peermgr.main`
- **Legacy File / Symbol:** `_sys/cli/peer_mgr.py`
- **Disposition:** `SPLIT`
- **Target Owner / API:** `peerhub.governance.peer_registry / peerhub.cli.peer`
- **Current Real Consumers (Empirically Measured):** _sys/tests/unit/test_peer_mgr_add.py, _sys/tests/unit/test_peer_mgr_missing_hub_nodes.py, _sys/tests/unit/test_peer_mgr_c10.py, _sys/docs-v2/general/routing.md, _sys/docs-v2/ops/architecture-audit-2026-07-24.md
- **State Read / Written:** Reads/writes _sys/ai/peers.json, orchestration.json, protocol.json, status.json, specific/*.md, transactions/*.json.
- **External Effects:** Acquires registry filelock; executes multi-file atomic transactions; modifies peer configuration across 5+ config files.
- **Compatibility Actions / Fixtures:** CLI commands split into 'peerhub peer add/remove/suspend/resume'; transaction logic moves to governance store; test_peer_mgr_c10 fixtures.
- **Retirement Condition:** Peer lifecycle managed through PeerHub configuration store.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 56: `mig.cli.peermgr.cmd_suspend_resume`
- **Legacy File / Symbol:** `_sys/cli/peer_mgr.py:cmd_suspend`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.governance.registry (peerhub peer suspend/resume)`
- **Current Real Consumers (Empirically Measured):** _sys/tests/unit/test_peer_mgr_missing_hub_nodes.py, _sys/docs-v2/ops/architecture-audit-2026-07-24.md, _sys/docs-v2/ops/backlog-design-consensus-2026-07-24.md
- **State Read / Written:** Updates orchestration.json nodes enabled status and governance voting lists atomically.
- **External Effects:** Persists node enablement and voting membership changes to disk.
- **Compatibility Actions / Fixtures:** CLI parity fixture 'fixture_peer_suspend_resume'.
- **Retirement Condition:** Node enablement controlled via PeerHub registry.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 57: `mig.cli.peermgr.cmd_add_remove`
- **Legacy File / Symbol:** `_sys/cli/peer_mgr.py:cmd_add`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.governance.registry (peerhub peer add/remove)`
- **Current Real Consumers (Empirically Measured):** _sys/tests/unit/test_peer_mgr_add.py, _sys/docs-v2/ops/backlog-design-consensus-2026-07-24.md, _sys/docs-v2/ops/architecture-audit-2026-07-24.md
- **State Read / Written:** Modifies peers.json, orchestration.json, protocol.json, and specific docs atomically.
- **External Effects:** Creates/deletes peer registrations and specific guidance files.
- **Compatibility Actions / Fixtures:** CLI parity fixture 'fixture_peer_add_remove'.
- **Retirement Condition:** Peer configuration store unified in PeerHub.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 58: `mig.cli.peermgr.transaction_engine`
- **Legacy File / Symbol:** `_sys/cli/peer_mgr.py:PeerMgrTransaction`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.storage.atomic_transaction`
- **Current Real Consumers (Empirically Measured):** _sys/tests/unit/test_peer_mgr_c10.py, _sys/docs-v2/ops/backlog-design-consensus-2026-07-24.md
- **State Read / Written:** Pre-commit SHA256 CAS baseline checks; writes transaction journal to _sys/ai/transactions/<id>.json; commits multi-file writes atomically.
- **External Effects:** Rolls back failed writes and cleans transaction journals.
- **Compatibility Actions / Fixtures:** Migrated to generic ACID storage transaction layer in PeerHub; fixture_transaction_rollback.
- **Retirement Condition:** PeerHub storage engine provides native multi-document CAS transactions.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 59: `mig.cli.peermgr.atomic_io`
- **Legacy File / Symbol:** `_sys/cli/peer_mgr.py:_write_json_atomic`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.storage.atomic_io`
- **Current Real Consumers (Empirically Measured):** _sys/core/hub.py, _sys/checks/canary_budget.py, _sys/tests/unit/test_broker_transaction_safety.py, _sys/docs-v2/ops/hub-mutation-broker.md
- **State Read / Written:** Atomic write with unique temp file, flush+fsync, and Windows PermissionError retries.
- **External Effects:** Writes and renames JSON files atomically on Windows filesystem.
- **Compatibility Actions / Fixtures:** Replace ad-hoc helper with peerhub.storage.atomic_io module.
- **Retirement Condition:** All core modules use PeerHub storage layer.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 60: `mig.cli.peermgr.lock_management`
- **Legacy File / Symbol:** `_sys/cli/peer_mgr.py:_get_lock`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.storage.lock`
- **Current Real Consumers (Empirically Measured):** _sys/core/hub.py, _sys/checks/canary_budget.py, _sys/tests/unit/test_lease_session_concurrency.py, _sys/docs-v2/ops/architecture-audit-2026-07-24.md
- **State Read / Written:** Acquires cross-process FileLock on _sys/ai/.lock/peer_mgr.lock with timeout.
- **External Effects:** Creates and locks lockfiles on filesystem.
- **Compatibility Actions / Fixtures:** Migrate to peerhub.storage.lock context manager.
- **Retirement Condition:** All registry mutations use PeerHub lock manager.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 61: `mig.cli.peermgr.validation_and_status`
- **Legacy File / Symbol:** `_sys/cli/peer_mgr.py:cmd_validate`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.governance.validator (peerhub peer validate/status)`
- **Current Real Consumers (Empirically Measured):** _sys/tests/unit/test_peer_mgr_missing_hub_nodes.py
- **State Read / Written:** Cross-validates consistency between peers.json, orchestration.json, and protocol.json; checks uncommitted transaction journals.
- **External Effects:** Prints validation report or recovers incomplete transactions.
- **Compatibility Actions / Fixtures:** CLI validation fixture 'fixture_peer_validate'.
- **Retirement Condition:** PeerHub configuration store enforces schema consistency on write.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 62: `mig.cli.diag.main_entry`
- **Legacy File / Symbol:** `_sys/cli/diag.py`
- **Disposition:** `SPLIT`
- **Target Owner / API:** `peerhub.cli.diag / peerhub.telemetry.diagnostics`
- **Current Real Consumers (Empirically Measured):** _sys/cli/diag.bat, _sys/core/hub.py, _sys/core/hub_logging.py, _sys/tests/unit/test_diag_cli.py, _sys/ai/user-directives.md, _sys/ai/telemetry-config.json
- **State Read / Written:** Reads _sys/ai/status.json, routing-config.json, peers.json, orchestration.json, ask_history.jsonl, cost-log.jsonl, leases.json.
- **External Effects:** Renders ANSI terminal dashboard; executes watch loop; emits JSON snapshots to stdout.
- **Compatibility Actions / Fixtures:** Break monolithic 2600-line CLI into modular renderers; fixture_diag_cli_e2e.
- **Retirement Condition:** 'peerhub diag' fully implements all telemetry and dashboard capabilities.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 63: `mig.cli.diag.live_hud_renderer`
- **Legacy File / Symbol:** `_sys/cli/diag.py:render_dashboard`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.cli.diag.dashboard`
- **Current Real Consumers (Empirically Measured):** _sys/tests/unit/test_diag_cli.py, _sys/docs/history/ops/pretdd-prep-2026-07-09.md, _sys/docs/history/ops/backlog-5whys-consensus-2026-07-08-round3.md
- **State Read / Written:** Aggregates peer health, quota pools, active sessions, and routing alerts into full-screen dashboard.
- **External Effects:** Outputs formatted ANSI dashboard to stdout.
- **Compatibility Actions / Fixtures:** Golden output fixture 'fixture_diag_dashboard_render'.
- **Retirement Condition:** Dashboard rendered via PeerHub telemetry UI module.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 64: `mig.cli.diag.summary_renderer`
- **Legacy File / Symbol:** `_sys/cli/diag.py:render_summary`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.cli.diag.summary`
- **Current Real Consumers (Empirically Measured):** _sys/tests/unit/test_diag_layout.py, _sys/tests/unit/test_diag_quota_format.py, _sys/tests/unit/test_c10_remaining_items.py, _sys/docs-v2/ops/mega-mece-audit-2026-07-16.md
- **State Read / Written:** Formats compact nearest-prompt summary header and quota continuation rows.
- **External Effects:** Outputs single/multi-line summary to stdout.
- **Compatibility Actions / Fixtures:** Golden output fixture 'fixture_diag_summary_render'.
- **Retirement Condition:** Summary rendered via PeerHub summary renderer.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 65: `mig.cli.diag.quota_headroom_renderer`
- **Legacy File / Symbol:** `_sys/cli/diag.py:render_live_quota_pools`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.telemetry.quota_analyzer`
- **Current Real Consumers (Empirically Measured):** _sys/tests/unit/test_diag_quota_format.py, _sys/tests/unit/test_diag_layout.py, _sys/docs-v2/ops/mega-mece-audit-2026-07-16.md
- **State Read / Written:** Calculates composite exhaustion index (EXH), urgency weights, and renders sorted quota pools.
- **External Effects:** Outputs quota pool status table.
- **Compatibility Actions / Fixtures:** Unit test suite test_diag_quota_format.py migrated to tests/unit/telemetry/test_quota_analyzer.py.
- **Retirement Condition:** Quota calculations owned by PeerHub telemetry engine.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 66: `mig.cli.diag.session_consumption_renderer`
- **Legacy File / Symbol:** `_sys/cli/diag.py:load_recent_session_consumption`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.telemetry.session_metrics`
- **Current Real Consumers (Empirically Measured):** _sys/tests/unit/test_recent_session_consumption.py, _sys/tests/unit/test_diag_cli.py, _sys/docs-v2/ops/pretdd-prep-2026-07-21-diag-quota-metrics.md
- **State Read / Written:** Reads cost-log.jsonl and leases.json; aggregates per-session token totals.
- **External Effects:** Outputs consumption summary table.
- **Compatibility Actions / Fixtures:** Test suite test_recent_session_consumption.py migrated to PeerHub telemetry suite.
- **Retirement Condition:** Cost logging and session consumption tracked in PeerHub telemetry store.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 67: `mig.cli.diag.peer_health_renderer`
- **Legacy File / Symbol:** `_sys/cli/diag.py:render_live_peer_health`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.cli.diag.health`
- **Current Real Consumers (Empirically Measured):** _sys/tests/unit/test_diag_cli.py, _sys/tests/unit/test_diag_layout.py
- **State Read / Written:** Reads peer records from telemetry snapshot; calculates health ranks and status tokens.
- **External Effects:** Outputs peer health status row.
- **Compatibility Actions / Fixtures:** Golden output fixture 'fixture_diag_peer_health'.
- **Retirement Condition:** Health rendering integrated into PeerHub diag health view.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 68: `mig.cli.diag.topology_accounting_renderer`
- **Legacy File / Symbol:** `_sys/cli/diag.py:render_profiles`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.cli.diag.accounting`
- **Current Real Consumers (Empirically Measured):** _sys/tests/unit/test_diag_layout.py, _sys/docs/history/ops/diag-redesign-design.md
- **State Read / Written:** Renders model routing topology (render_profiles), redacted account views (render_accounts), token histories (render_tokens), and ask history stats (render_usage).
- **External Effects:** Outputs topology and accounting tables to stdout.
- **Compatibility Actions / Fixtures:** Test suite test_diag_layout.py migrated to PeerHub layout tests.
- **Retirement Condition:** Profile topology and usage views migrated to PeerHub.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 69: `mig.cli.diag.policy_project_renderer`
- **Legacy File / Symbol:** `_sys/cli/diag.py:render_policy`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.cli.diag.policy`
- **Current Real Consumers (Empirically Measured):** _sys/cli/diag.py internal
- **State Read / Written:** Reads operational config paths (render_policy); runs bounded git status check (render_project).
- **External Effects:** Outputs effective operational knobs and working-tree status.
- **Compatibility Actions / Fixtures:** Integrated into 'peerhub diag --policy' and 'peerhub diag --project'.
- **Retirement Condition:** Policy rendering migrated to PeerHub policy inspector.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 70: `mig.cli.diag.json_snapshot_exporter`
- **Legacy File / Symbol:** `_sys/cli/diag.py:emit_json_snapshot`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.telemetry.exporter`
- **Current Real Consumers (Empirically Measured):** _sys/tests/unit/test_diag_cli.py
- **State Read / Written:** Builds and serializes complete telemetry snapshot dictionary to JSON.
- **External Effects:** Prints JSON string to stdout.
- **Compatibility Actions / Fixtures:** Snapshot contract fixture 'fixture_diag_json_snapshot'.
- **Retirement Condition:** Telemetry JSON schema versioned and emitted by 'peerhub diag --json'.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

### Row 71: `mig.cli.diag.watch_engine`
- **Legacy File / Symbol:** `_sys/cli/diag.py:run_watch`
- **Disposition:** `REPLACE`
- **Target Owner / API:** `peerhub.cli.diag.watch`
- **Current Real Consumers (Empirically Measured):** _sys/tests/unit/test_diag_cli.py, _sys/ai/backlog.json
- **State Read / Written:** Maintains refresh timer; reads non-blocking Windows console key presses.
- **External Effects:** Double-buffered in-place ANSI console repainting with flicker-free blitting.
- **Compatibility Actions / Fixtures:** Interactive watch loop fixture 'fixture_diag_watch_tick'.
- **Retirement Condition:** Watch mode implemented in PeerHub CLI runner.
- *[Reserved] `adapter_feature`:* None (unpopulated)
- *[Reserved] `coverage_case_id`:* TBD

---

## 4. Master Crosswalk Matrix Table

| # | Migration Capability ID | Legacy File / Symbol | Disposition | Target Owner / API | Measured Consumers |
|---|---|---|---|---|---|
| 1 | `mig.cli.shim.posix_bash_bridge` | `_sys/cli/_bat-shim` | `replace` | `peerhub.cli.compat.shim / explicit shim lifecycle manager` | 12 POSIX bash wrapper scripts (_sys/cli/agy, batch-review, c... |
| 2 | `mig.cli.shim.agy_posix` | `_sys/cli/agy` | `deprecate` | `peerhub.cli.compat / peerhub console ag` | Interactive Git-Bash users; referenced in _sys/ai/infra.json... |
| 3 | `mig.cli.shim.batch_review_posix` | `_sys/cli/batch-review` | `stay` | `Engram host review toolchain (out of PeerHub core)` | Interactive Git-Bash users; _sys/docs-v2/user/manual.md, _sy... |
| 4 | `mig.cli.shim.claude_posix` | `_sys/cli/claude` | `deprecate` | `peerhub.cli.compat / peerhub console cc` | Interactive Git-Bash users; _sys/ai/governance_params.json, ... |
| 5 | `mig.cli.shim.codex_posix` | `_sys/cli/codex` | `deprecate` | `peerhub.cli.compat / peerhub console cx` | Interactive Git-Bash users; _sys/ai/model-registry.json, _sy... |
| 6 | `mig.cli.shim.collab_rate_gate_posix` | `_sys/cli/collab-rate-gate` | `deprecate` | `peerhub-engram bridge / Engram host governance` | Git hooks / bash scripts checking collaboration threshold; _... |
| 7 | `mig.cli.shim.diag_posix` | `_sys/cli/diag` | `replace` | `peerhub.cli (peerhub diag)` | Terminal operators running diag; _sys/ai/common/statusline/s... |
| 8 | `mig.cli.shim.git_draft_posix` | `_sys/cli/git-draft` | `stay` | `Engram host developer tooling` | Developers running git-draft; _sys/docs-v2/ops/conventions.m... |
| 9 | `mig.cli.shim.hub_posix` | `_sys/cli/hub` | `replace` | `peerhub.cli (peerhub)` | Subagent skills and bash orchestration; _sys/ai/common/skill... |
| 10 | `mig.cli.shim.launch_posix` | `_sys/cli/launch` | `stay` | `Engram host portable launcher` | Terminal operators; _sys/ai/infra.json, _sys/ai/peers.json, ... |
| 11 | `mig.cli.shim.manage_posix` | `_sys/cli/manage` | `stay` | `Engram host environment manager` | Terminal operators; _sys/ai/infra.json, _sys/checks/check_cl... |
| 12 | `mig.cli.shim.msg_posix` | `_sys/cli/msg` | `replace` | `peerhub.cli (peerhub ask / peerhub send)` | Peer scripts and collaboration loops; _sys/ai/collaboration_... |
| 13 | `mig.cli.shim.set_collab_rate_posix` | `_sys/cli/set-collab-rate` | `deprecate` | `peerhub-engram bridge / Engram policy manager` | Terminal operators; _sys/ai/infra.json, _sys/docs-v2/user/ma... |
| 14 | `mig.cli.wrapper.agy_bat` | `_sys/cli/agy.bat` | `replace` | `peerhub.cli.compat / peerhub console ag` | Windows console users; _sys/cli/agy, _sys/ai/infra.json, _sy... |
| 15 | `mig.cli.wrapper.batch_review_bat` | `_sys/cli/batch-review.bat` | `stay` | `Engram host review toolchain` | Windows operators / hook callers; _sys/cli/batch-review, Eng... |
| 16 | `mig.cli.wrapper.claude_bat` | `_sys/cli/claude.bat` | `replace` | `peerhub.cli.compat / peerhub console cc` | Windows console users; _sys/cli/claude, _sys/ai/infra.json, ... |
| 17 | `mig.cli.wrapper.codex_bat` | `_sys/cli/codex.bat` | `replace` | `peerhub.cli.compat / peerhub console cx` | Windows console users; _sys/cli/codex, _sys/ai/infra.json, _... |
| 18 | `mig.cli.wrapper.collab_rate_gate_bat` | `_sys/cli/collab-rate-gate.bat` | `deprecate` | `peerhub-engram bridge / Engram git hooks` | Git pre-commit/stop hooks; _sys/cli/collab-rate-gate, _sys/a... |
| 19 | `mig.cli.wrapper.diag_bat` | `_sys/cli/diag.bat` | `replace` | `peerhub.cli (peerhub diag)` | Terminal operators; _sys/cli/diag, _sys/docs-v2/ops/logging.... |
| 20 | `mig.cli.wrapper.git_draft_bat` | `_sys/cli/git-draft.bat` | `stay` | `Engram host git utilities` | Windows developers; _sys/cli/git-draft, _sys/docs-v2/ops/con... |
| 21 | `mig.cli.wrapper.hub_bat` | `_sys/cli/hub.bat` | `replace` | `peerhub.cli` | CLI operators and automated checks; _sys/cli/hub, _sys/ai/in... |
| 22 | `mig.cli.wrapper.launch_bat` | `_sys/cli/launch.bat` | `stay` | `Engram host portable launcher` | Windows operators; _sys/cli/launch, _sys/checks/check_deps.p... |
| 23 | `mig.cli.wrapper.manage_bat` | `_sys/cli/manage.bat` | `stay` | `Engram host environment manager` | Windows operators; _sys/cli/manage, _sys/checks/check_deps.p... |
| 24 | `mig.cli.wrapper.msg_bat` | `_sys/cli/msg.bat` | `replace` | `peerhub.cli (peerhub ask / peerhub send / peerhub mailbox)` | Legacy peer IPC; _sys/cli/msg, _sys/ai/infra.json, _sys/ai/p... |
| 25 | `mig.cli.wrapper.peerhub_bat` | `_sys/cli/peerhub.bat` | `stay` | `peerhub.cli (canonical Windows launcher)` | Windows operators and wrapper scripts (_sys/cli/diag.bat, _s... |
| 26 | `mig.cli.wrapper.set_collab_rate_bat` | `_sys/cli/set-collab-rate.bat` | `deprecate` | `peerhub-engram bridge / Engram policy tool` | Windows operators; _sys/cli/set-collab-rate, _sys/ai/infra.j... |
| 27 | `mig.cli.entry.agy` | `_sys/cli/agy_entry.py` | `replace` | `peerhub.cli.console / peerhub.adapters.agy` | _sys/cli/agy.bat, _sys/docs-v2/specific/ag.md, _sys/docs-v2/... |
| 28 | `mig.cli.entry.claude` | `_sys/cli/claude_entry.py` | `replace` | `peerhub.cli.console / peerhub.adapters.claude` | _sys/cli/claude.bat, _sys/docs-v2/ops/backlog-design-consens... |
| 29 | `mig.cli.entry.codex` | `_sys/cli/codex_entry.py` | `replace` | `peerhub.cli.console / peerhub.adapters.codex` | _sys/cli/codex.bat, _sys/core/snapshot.py, _sys/docs-v2/spec... |
| 30 | `mig.cli.util.ag_statusline` | `_sys/cli/ag_statusline.py` | `replace` | `peerhub.telemetry.statusline / peerhub.adapters.agy` | _sys/tests/unit/test_t12_t13_misc.py, _sys/docs/history/spec... |
| 31 | `mig.cli.util.cleanup_shim` | `_sys/cli/cleanup.py` | `split` | `Engram host scrubber / peerhub.storage.cleanup` | _sys/cli/manage.py, _sys/core/dispatcher.py, _sys/checks/che... |
| 32 | `mig.cli.util.cleanup_run_cleanup` | `_sys/cli/cleanup.py:run_cleanup` | `split` | `core.scrubber (Engram host) / peerhub.storage.cleanup` | _sys/cli/manage.py, _sys/checks/check_deps.py... |
| 33 | `mig.cli.util.git_draft` | `_sys/cli/git_draft.py` | `stay` | `Engram host developer tooling (out of PeerHub core)` | _sys/cli/git-draft.bat, _sys/cli/batch_review.py (imports _g... |
| 34 | `mig.cli.util.git_draft_get_diff` | `_sys/cli/git_draft.py:_get_diff` | `stay` | `Engram host developer tooling` | _sys/cli/batch_review.py... |
| 35 | `mig.cli.util.launcher_shim` | `_sys/cli/launcher.py` | `deprecate` | `core.launcher (Engram host launcher)` | _sys/checks/check_cli_reality.py, _sys/core/relocator.py, _s... |
| 36 | `mig.cli.review.batch_review_main` | `_sys/cli/batch_review.py` | `stay` | `Engram host review toolchain (out of PeerHub core)` | _sys/cli/batch-review.bat, _sys/tests/unit/test_t12_t13_misc... |
| 37 | `mig.cli.review.policy_loader` | `_sys/cli/batch_review.py:_load_collab_policy` | `split` | `peerhub-engram bridge / Engram policy manager` | _sys/cli/batch_review.py internal... |
| 38 | `mig.cli.review.time_gate` | `_sys/cli/batch_review.py:_time_gate_ok` | `stay` | `Engram host review toolchain` | _sys/cli/batch_review.py internal... |
| 39 | `mig.cli.review.git_diff_extractor` | `_sys/cli/batch_review.py:_get_diff` | `stay` | `Engram host review toolchain` | _sys/cli/batch_review.py internal... |
| 40 | `mig.cli.manage.manage_main` | `_sys/cli/manage.py` | `split` | `Engram host environment manager (core.virtualizer, core.registrar)` | _sys/cli/manage.bat, _sys/checks/check_deps.py, _sys/core/hu... |
| 41 | `mig.cli.manage.subst_mappings` | `_sys/cli/manage.py:get_subst_mappings` | `stay` | `core.virtualizer (Engram host)` | _sys/tests/unit/test_launcher_paths.py, _sys/ai/unreferenced... |
| 42 | `mig.cli.manage.workspace_init_legacy` | `_sys/cli/manage.py:_workspace_init_legacy` | `deprecate` | `Engram host workspace provisioner` | Legacy manage.py fallback... |
| 43 | `mig.cli.runner.run_console_session` | `_sys/cli/console_runner.py` | `replace` | `peerhub.cli.console / peerhub.engine.interactive_runner` | _sys/cli/agy_entry.py, _sys/cli/claude_entry.py, _sys/cli/co... |
| 44 | `mig.cli.runner.lease_duty_classifier` | `_sys/cli/console_runner.py:should_claim_lease` | `replace` | `peerhub.coordination.lease_policy` | _sys/tests/unit/test_console_runner_s3.py... |
| 45 | `mig.cli.runner.terminal_lease_client` | `_sys/cli/console_runner.py:_claim_terminal_lease` | `replace` | `peerhub.coordination.lease_manager` | _sys/tests/unit/test_console_runner_s3.py... |
| 46 | `mig.cli.runner.heartbeat_renew` | `_sys/cli/console_runner.py:_renew_heartbeat` | `replace` | `peerhub.coordination.lease_manager` | _sys/tests/unit/test_console_runner_s3.py... |
| 47 | `mig.cli.runner.health_update` | `_sys/cli/console_runner.py:_update_peer_health_json` | `replace` | `peerhub.health.manager` | _sys/tests/unit/test_console_runner_s3.py... |
| 48 | `mig.cli.runner.types` | `_sys/cli/console_runner.py:ConsoleSessionSpec` | `replace` | `peerhub.types.console` | _sys/tests/unit/test_console_runner_s3.py, _sys/cli/agy_entr... |
| 49 | `mig.cli.console.prepare_console_launch` | `_sys/cli/peer_console.py:prepare_console_launch` | `replace` | `peerhub.adapters.base.ConsoleClassifier / peerhub.cli.console` | _sys/cli/console_runner.py, _sys/tests/unit/test_peer_consol... |
| 50 | `mig.cli.console.peer_default_args` | `_sys/cli/peer_console.py:peer_default_args` | `replace` | `peerhub.adapters.contract` | _sys/core/hub.py, _sys/tests/unit/test_peer_console_c8a.py, ... |
| 51 | `mig.cli.console.apply_security_semantics` | `_sys/cli/peer_console.py:apply_security_semantics` | `replace` | `peerhub.adapters.security_translator` | _sys/checks/check_cli_reality.py, _sys/tests/unit/test_check... |
| 52 | `mig.cli.console.security_validation_error` | `_sys/cli/peer_console.py:SecurityValidationError` | `replace` | `peerhub.errors.SecurityValidationError` | _sys/cli/console_runner.py, _sys/tests/unit/test_console_run... |
| 53 | `mig.cli.console.types` | `_sys/cli/peer_console.py:InvocationKind` | `replace` | `peerhub.types.invocation` | _sys/cli/console_runner.py, _sys/tests/unit/test_console_run... |
| 54 | `mig.cli.console.interactive_profile_banner` | `_sys/cli/peer_console.py:interactive_profile_banner` | `deprecate` | `peerhub.cli.ui` | _sys/ai/unreferenced_functions_baseline.json... |
| 55 | `mig.cli.peermgr.main` | `_sys/cli/peer_mgr.py` | `split` | `peerhub.governance.peer_registry / peerhub.cli.peer` | _sys/tests/unit/test_peer_mgr_add.py, _sys/tests/unit/test_p... |
| 56 | `mig.cli.peermgr.cmd_suspend_resume` | `_sys/cli/peer_mgr.py:cmd_suspend` | `replace` | `peerhub.governance.registry (peerhub peer suspend/resume)` | _sys/tests/unit/test_peer_mgr_missing_hub_nodes.py, _sys/doc... |
| 57 | `mig.cli.peermgr.cmd_add_remove` | `_sys/cli/peer_mgr.py:cmd_add` | `replace` | `peerhub.governance.registry (peerhub peer add/remove)` | _sys/tests/unit/test_peer_mgr_add.py, _sys/docs-v2/ops/backl... |
| 58 | `mig.cli.peermgr.transaction_engine` | `_sys/cli/peer_mgr.py:PeerMgrTransaction` | `replace` | `peerhub.storage.atomic_transaction` | _sys/tests/unit/test_peer_mgr_c10.py, _sys/docs-v2/ops/backl... |
| 59 | `mig.cli.peermgr.atomic_io` | `_sys/cli/peer_mgr.py:_write_json_atomic` | `replace` | `peerhub.storage.atomic_io` | _sys/core/hub.py, _sys/checks/canary_budget.py, _sys/tests/u... |
| 60 | `mig.cli.peermgr.lock_management` | `_sys/cli/peer_mgr.py:_get_lock` | `replace` | `peerhub.storage.lock` | _sys/core/hub.py, _sys/checks/canary_budget.py, _sys/tests/u... |
| 61 | `mig.cli.peermgr.validation_and_status` | `_sys/cli/peer_mgr.py:cmd_validate` | `replace` | `peerhub.governance.validator (peerhub peer validate/status)` | _sys/tests/unit/test_peer_mgr_missing_hub_nodes.py... |
| 62 | `mig.cli.diag.main_entry` | `_sys/cli/diag.py` | `split` | `peerhub.cli.diag / peerhub.telemetry.diagnostics` | _sys/cli/diag.bat, _sys/core/hub.py, _sys/core/hub_logging.p... |
| 63 | `mig.cli.diag.live_hud_renderer` | `_sys/cli/diag.py:render_dashboard` | `replace` | `peerhub.cli.diag.dashboard` | _sys/tests/unit/test_diag_cli.py, _sys/docs/history/ops/pret... |
| 64 | `mig.cli.diag.summary_renderer` | `_sys/cli/diag.py:render_summary` | `replace` | `peerhub.cli.diag.summary` | _sys/tests/unit/test_diag_layout.py, _sys/tests/unit/test_di... |
| 65 | `mig.cli.diag.quota_headroom_renderer` | `_sys/cli/diag.py:render_live_quota_pools` | `replace` | `peerhub.telemetry.quota_analyzer` | _sys/tests/unit/test_diag_quota_format.py, _sys/tests/unit/t... |
| 66 | `mig.cli.diag.session_consumption_renderer` | `_sys/cli/diag.py:load_recent_session_consumption` | `replace` | `peerhub.telemetry.session_metrics` | _sys/tests/unit/test_recent_session_consumption.py, _sys/tes... |
| 67 | `mig.cli.diag.peer_health_renderer` | `_sys/cli/diag.py:render_live_peer_health` | `replace` | `peerhub.cli.diag.health` | _sys/tests/unit/test_diag_cli.py, _sys/tests/unit/test_diag_... |
| 68 | `mig.cli.diag.topology_accounting_renderer` | `_sys/cli/diag.py:render_profiles` | `replace` | `peerhub.cli.diag.accounting` | _sys/tests/unit/test_diag_layout.py, _sys/docs/history/ops/d... |
| 69 | `mig.cli.diag.policy_project_renderer` | `_sys/cli/diag.py:render_policy` | `replace` | `peerhub.cli.diag.policy` | _sys/cli/diag.py internal... |
| 70 | `mig.cli.diag.json_snapshot_exporter` | `_sys/cli/diag.py:emit_json_snapshot` | `replace` | `peerhub.telemetry.exporter` | _sys/tests/unit/test_diag_cli.py... |
| 71 | `mig.cli.diag.watch_engine` | `_sys/cli/diag.py:run_watch` | `replace` | `peerhub.cli.diag.watch` | _sys/tests/unit/test_diag_cli.py, _sys/ai/backlog.json... |
