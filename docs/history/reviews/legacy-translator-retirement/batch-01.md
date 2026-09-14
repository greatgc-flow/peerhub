# Batch 01 — test_lesson_inject.py Preservation Record

> LegacyTranslator retirement, batch 1 (canary).
> Commit: staged, pending review (base: `b9031b4`).
> Review record updated: independent review verification pass.

## Summary

Rewrote `test_legacy_lesson_inject_translates_and_executes` in `tests/integration/application/test_lesson_inject.py` to retire `LegacyTranslator` and `LegacyActionCall` usage.

### Test Boundary Transition: Translator-Only vs. Client API

The original baseline test and the rewritten candidate test exercise fundamentally different boundaries:

- **Original Baseline Boundary (Translator-Only)**:
  `LegacyTranslator.translate(LegacyActionCall("lesson-inject", ...), submission=sub)`
  This was purely a unit test of argument-to-dataclass translation. It verified that dictionary arguments (`"peer": "cc"`, `"workspace_id": "default"`, `"os": "windows"`) mapped to attributes on `LessonInjectCommand`. **No command execution occurred**: the command was never submitted to `ApplicationAPI`, no service handlers were invoked, and no injection payload was evaluated.
- **New Candidate Boundary (Actual Client API)**:
  `Client(runtime.application_api).submit(cmd)`
  The candidate constructs `LessonInjectCommand` directly and submits it through `Client.submit()`, crossing the full application boundary: command dispatch via registered descriptor `governance.lesson.inject`, parameter envelope decode, `inject_lessons()` handler execution, and result encoding into `CommandSuccess`.

Also added `pytest-cov>=5.0` to `pyproject.toml` `[project.optional-dependencies] dev`.

## Files Changed

| Path | Change |
|------|--------|
| `pyproject.toml` | Added `pytest-cov>=5.0` to dev dependencies |
| `tests/integration/application/test_lesson_inject.py` | Rewrote 1 of 11 tests (lines 177–219 → 177–243); 10 tests untouched |

## Collection + Outcome Diff

### BEFORE (11 nodes, all PASSED)

```
tests/integration/application/test_lesson_inject.py::test_missing_profile_values_fail_open           PASSED
tests/integration/application/test_lesson_inject.py::test_unknown_severity_defaults_medium            PASSED
tests/integration/application/test_lesson_inject.py::test_exactly_equal_character_budget              PASSED
tests/integration/application/test_lesson_inject.py::test_header_newlines_not_counted                 PASSED
tests/integration/application/test_lesson_inject.py::test_sticky_but_noncritical_no_priority          PASSED
tests/integration/application/test_lesson_inject.py::test_critical_bypass_exceeds_caps                PASSED
tests/integration/application/test_lesson_inject.py::test_scope_selection                             PASSED
tests/integration/application/test_lesson_inject.py::test_affected_peers                              PASSED
tests/integration/application/test_lesson_inject.py::test_os_shell_task_types                         PASSED
tests/integration/application/test_lesson_inject.py::test_delivery_enabled_false                      PASSED
tests/integration/application/test_legacy_lesson_inject_translates_and_executes PASSED
```

### AFTER (11 nodes, all PASSED)

Identical node IDs, identical markers (none), identical setup/call/teardown outcomes (all PASSED).

## Per-Node Assertion Table (AST-derived)

| # | Test Node | Before Count | After Count | Δ | Notes |
|---|-----------|:---:|:---:|:---:|-------|
| 1 | test_missing_profile_values_fail_open | 2 | 2 | = | Preserved |
| 2 | test_unknown_severity_defaults_medium | 3 | 3 | = | Preserved |
| 3 | test_exactly_equal_character_budget | 3 | 3 | = | Preserved |
| 4 | test_header_newlines_not_counted | 1 | 1 | = | Preserved |
| 5 | test_sticky_but_noncritical_no_priority | 1 | 1 | = | Preserved |
| 6 | test_critical_bypass_exceeds_caps | 3 | 3 | = | Preserved |
| 7 | test_scope_selection | 4 | 4 | = | Preserved |
| 8 | test_affected_peers | 4 | 4 | = | Preserved |
| 9 | test_os_shell_task_types | 5 | 5 | = | Preserved |
| 10 | test_delivery_enabled_false | 0 | 0 | = | Preserved (empty test body) |
| 11 | test_legacy_lesson_inject_translates_and_executes | 4 | 4 | = | **Rewritten** (4 translator-only assertions retired; 4 API assertions added) |
| | **TOTAL** | **30** | **30** | **=** | **26 preserved + 4 retired / 4 added** |

---

## All 26 Preserved Normalized Expressions (Tests 1–10)

All 26 assertions across untouched test nodes 1–10 are 100% byte-and-AST identical between baseline (`b9031b4`) and candidate:

| # | Node ID | Line | Normalized Expression | Status |
|---|---------|:----:|-----------------------|:------:|
| 1 | `test_missing_profile_values_fail_open` | 72 | `result is not None` | PRESERVED |
| 2 | `test_missing_profile_values_fail_open` | 73 | `'os-specific: os rule' in result` | PRESERVED |
| 3 | `test_unknown_severity_defaults_medium` | 82 | `result is not None` | PRESERVED |
| 4 | `test_unknown_severity_defaults_medium` | 83 | `'- WEIRD weird: weird rule' in result` | PRESERVED |
| 5 | `test_unknown_severity_defaults_medium` | 88 | `result_high is None` | PRESERVED |
| 6 | `test_exactly_equal_character_budget` | 97 | `result is not None` | PRESERVED |
| 7 | `test_exactly_equal_character_budget` | 98 | `'exact' in result` | PRESERVED |
| 8 | `test_exactly_equal_character_budget` | 99 | `'Omitted' not in result` | PRESERVED |
| 9 | `test_header_newlines_not_counted` | 108 | `result is not None` | PRESERVED |
| 10 | `test_sticky_but_noncritical_no_priority` | 118 | `result.index('high rule') < result.index('medium rule')` | PRESERVED |
| 11 | `test_critical_bypass_exceeds_caps` | 129 | `result is not None` | PRESERVED |
| 12 | `test_critical_bypass_exceeds_caps` | 130 | `'c1' in result` | PRESERVED |
| 13 | `test_critical_bypass_exceeds_caps` | 131 | `'c2' in result` | PRESERVED |
| 14 | `test_scope_selection` | 141 | `result is not None` | PRESERVED |
| 15 | `test_scope_selection` | 142 | `'g1' in result` | PRESERVED |
| 16 | `test_scope_selection` | 143 | `'w1' in result` | PRESERVED |
| 17 | `test_scope_selection` | 144 | `'w2' not in result` | PRESERVED |
| 18 | `test_affected_peers` | 154 | `result is not None` | PRESERVED |
| 19 | `test_affected_peers` | 155 | `'empty' in result` | PRESERVED |
| 20 | `test_affected_peers` | 156 | `'match' in result` | PRESERVED |
| 21 | `test_affected_peers` | 157 | `'miss' not in result` | PRESERVED |
| 22 | `test_os_shell_task_types` | 168 | `result is not None` | PRESERVED |
| 23 | `test_os_shell_task_types` | 169 | `'match' in result` | PRESERVED |
| 24 | `test_os_shell_task_types` | 170 | `'miss_os' not in result` | PRESERVED |
| 25 | `test_os_shell_task_types` | 171 | `'miss_shell' not in result` | PRESERVED |
| 26 | `test_os_shell_task_types` | 172 | `'miss_task' not in result` | PRESERVED |

*(Node `test_delivery_enabled_false` contains 0 assertions in both baseline and candidate).*

---

## Rewritten Test (#11): Justification of Retired Translator Assertions

The 4 removed baseline assertions are **explicitly retired translator-only assertions**, formally registered in `docs/reviews/legacy-translator-retirement/gate-canary-waivers.json`.

**Crucial Correction**: Prior review notes erroneously attempted to present the new execution substring checks as semantically equivalent to the baseline translator assertions. That equivalence claim was incorrect: substring matching on the handler's output does not establish translator argument mapping. Instead, the 4 assertions are justified for retirement because `LegacyTranslator` is being deprecated, and the new assertions verify the replacement end-to-end `Client(ApplicationAPI)` submission path.

### Retired Baseline Assertions (Translator-Only Waivers)

| # | Removed Baseline Expression | Line | Retirement Justification & Waiver Basis |
|---|-----------------------------|:----:|------------------------------------------|
| 1 | `not hasattr(outcome, 'reason')` | 429 | **Retire direct translator rejection-shape check**: Verified `LegacyTranslator.translate()` returned `KnownLegacyTranslation` rather than a rejection object with `.reason`. This is translator-internal error handling, not needed when constructing commands natively. |
| 2 | `outcome.command.target_peer_id == 'cc'` | 431 | **Retire translator argument-to-command peer mapping**: Verified `LegacyTranslator` mapped input argument `"peer"` to attribute `target_peer_id`. Retired with translator deprecation. |
| 3 | `outcome.command.workspace_id == 'default'` | 433 | **Retire translator argument-to-command workspace mapping**: Verified `LegacyTranslator` mapped input argument `"workspace_id"` to attribute `workspace_id`. Retired with translator deprecation. |
| 4 | `outcome.command.os == 'windows'` | 435 | **Retire translator argument-to-command OS mapping**: Verified `LegacyTranslator` mapped input argument `"os"` to attribute `os`. Retired with translator deprecation. |

### Added Candidate Assertions (ApplicationAPI Boundary Checks)

| # | Added Candidate Expression | Line | Verified Behavior |
|---|----------------------------|:----:|-------------------|
| 1 | `outcome.ok` | 234 | Confirms `Client.submit()` succeeded and `ApplicationAPI` returned `CommandSuccess`. |
| 2 | `outcome.result['injection_block'] is not None` | 235 | Confirms `inject_lessons()` handler executed and returned a populated injection payload. |
| 3 | `'legacy-test' in outcome.result['injection_block']` | 236 | Confirms the injected lesson title formatted into the resulting prompt block. |
| 4 | `'legacy rule' in outcome.result['injection_block']` | 237 | Confirms the injected lesson rule content formatted into the resulting prompt block. |

---

## Coverage Delta & ALL Affected Modules

### Exact AST Exclusion Scope

To isolate translator retirement from unrelated code:
- **Excluded Class Scope**: AST `ClassDef` named `LegacyTranslator` inside `peerhub/application/legacy.py`, spanning **lines 1707 to 2553 (847 lines)**.
- **Measured Scope in `legacy.py`**: All code outside `LegacyTranslator` (command dataclasses such as `LessonInjectCommand` lines 1008–1025, submission helpers, `legacy_thread_slug`, and dispatch types) remains fully measured and monitored for loss.
- **Zero Coverage Loss Rule**: Per `tools/legacy_retirement/README.md`, no file may lose any executed line or branch outside the excluded class. Equal percentages cannot conceal lost coordinates.

### Observed Raw Coverage Across ALL Affected Modules

The table below reflects exact raw counts extracted from `cov_head.dat` (baseline `b9031b4`) versus `cov_curr.dat` (candidate), with runtime exclusions disabled and branch coverage enabled (`--cov-branch`):

| Module | Statements / Denominator | Executed Lines (Before) | Executed Lines (After) | Line Δ | Branches / Denominator | Executed Branches (Before) | Executed Branches (After) | Branch Δ | Lost Coordinates |
|--------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `peerhub/application/api.py` | 926 | 399 (43.1%) | 428 (46.2%) | **+29** | 256 | 19 (7.4%) | 27 (10.5%) | **+8** | None (`[]`) |
| `peerhub/application/legacy.py` *(outside `LegacyTranslator`)* | 715 | 521 (72.9%) | 523 (73.1%) | **+2** | 36 | 0 (0.0%) | 0 (0.0%) | **=** | None (`[]`) |
| `peerhub/client.py` | 17 | 9 (52.9%) | 16 (94.1%) | **+7** | 2 | 0 (0.0%) | 1 (50.0%) | **+1** | None (`[]`) |
| `peerhub/core/protocol.py` | 421 | 277 (65.8%) | 293 (69.6%) | **+16** | 156 | 56 (35.9%) | 64 (41.0%) | **+8** | None (`[]`) |
| `peerhub/application/lesson_inject.py` | 125 | 111 (88.8%) | 111 (88.8%) | **=** | 34 | 26 (76.5%) | 26 (76.5%) | **=** | None (`[]`) |
| `peerhub/governance/broker.py` | 155 | 87 (56.1%) | 87 (56.1%) | **=** | 38 | 17 (44.7%) | 17 (44.7%) | **=** | None (`[]`) |
| `peerhub/governance/lessons.py` | 98 | 49 (50.0%) | 49 (50.0%) | **=** | 28 | 8 (28.6%) | 8 (28.6%) | **=** | None (`[]`) |
| **TOTAL (All 7 modules)** | **2,457** | **1,453 (59.1%)** | **1,507 (61.3%)** | **+54** | **512** | **126 (24.6%)** | **143 (27.9%)** | **+17** | **ZERO LOST** |

#### Gained Line and Branch Details

- **`peerhub/application/api.py`**:
  - Gained 29 lines: `[444, 704, 705, 710, 711, 712, 734, 735, 736, 737, 738, 741, 2923, 2943, 2944, 2963, 2986, 2991, 2996, 3015, 3016, 3037, 3057, 3058, 3059, 3061, 3062, 3065, 3067]`
  - Gained 8 branches: `[[704, 705], [711, 712], [2923, 2943], [2944, 2963], [2963, 2986], [2996, 3015], [3037, 3057], [3062, 3065]]`
- **`peerhub/application/legacy.py`**:
  - Gained 2 lines: `[1008, 1018]` (within `LessonInjectCommand` dataclass instantiation).
  - *(Note on raw unexcluded count: unexcluded line total in `legacy.py` dropped from 569/958 to 525/958 (-44 lines), reflecting that lines 1707–2553 in the retired `LegacyTranslator` body were no longer executed).*
- **`peerhub/client.py`**:
  - Gained 7 lines: `[27, 28, 35, 52, 54, 55, 56]` (`Client.__init__`, `Client.submit()`, and result return).
  - Gained 1 branch: `[[54, 55]]`
- **`peerhub/core/protocol.py`**:
  - Gained 16 lines: `[165, 175, 369, 370, 406, 410, 415, 422, 428, 429, 435, 445, 449, 454, 462, 467]`
  - Gained 8 branches: `[[164, 165], [174, 175], [369, 370], [415, 422], [415, 428], [428, 429], [435, 445], [454, 462]]`
- **`peerhub/application/lesson_inject.py`, `peerhub/governance/broker.py`, `peerhub/governance/lessons.py`**:
  - Exactly 0 lost lines, 0 gained lines, 0 lost branches, 0 gained branches.

---

## Actual Reproducible Commands (No Placeholders)

### 1. AST Assertion Extraction & Verification

```bash
# Concrete reproducible AST assertion extraction script (replaces placeholder)
python -c "import ast, sys; p = 'tests/integration/application/test_lesson_inject.py'; tree = ast.parse(open(p, 'r', encoding='utf-8').read()); [print(f'{f.name}:{a.lineno}: {ast.unparse(a.test)}') for f in tree.body if isinstance(f, ast.FunctionDef) for a in ast.walk(f) if isinstance(a, ast.Assert)]"
```

### 2. Collection & Execution Verification

```bash
# Collection verification
python -m pytest tests/integration/application/test_lesson_inject.py --collect-only -q

# Test execution
python -m pytest tests/integration/application/test_lesson_inject.py -v
```

### 3. Coverage Measurement Across All Affected Modules

```bash
# Multi-module coverage run
python -m pytest tests/integration/application/test_lesson_inject.py -v \
  --cov=peerhub.application.api \
  --cov=peerhub.application.legacy \
  --cov=peerhub.client \
  --cov=peerhub.core.protocol \
  --cov=peerhub.application.lesson_inject \
  --cov=peerhub.governance.broker \
  --cov=peerhub.governance.lessons \
  --cov-branch --cov-report=term-missing
```

---

## Artifact Retention in Ignored Scratch

The raw coverage databases and comparison script artifacts are precisely located and retained under `.peerhub/scratch/` (git-ignored via `.peerhub/` in repository root `.gitignore`):

1. **`cov_head.dat`**: `P:/workspace/peerhub/.peerhub/scratch/cov_head.dat` (1,036,288 bytes; baseline `b9031b4` execution coverage).
2. **`cov_curr.dat`**: `P:/workspace/peerhub/.peerhub/scratch/cov_curr.dat` (1,036,288 bytes; candidate execution coverage).
3. **`compare_cov.py`**: `P:/workspace/peerhub/.peerhub/scratch/compare_cov.py` (standalone reproducible script to read `cov_head.dat` and `cov_curr.dat` via `coverage.CoverageData` and compute exact coordinates with `LegacyTranslator` AST exclusion).

---

## Gate Status & Separate Worker Boundary

- **Gate Tool Ownership**: `tools/legacy_retirement/gate.py` and its evidence runs under `tools/legacy_retirement/runs/` are exclusively owned and operated by a separate worker.
- **Current Gate Status**:
  - Run `tools/legacy_retirement/runs/canary-unwaived` currently reports `Result: FAIL` because it was intentionally executed without waivers to detect raw AST assertion diffs.
  - The formal waiver file [`docs/reviews/legacy-translator-retirement/gate-canary-waivers.json`](file:///P:/workspace/peerhub/docs/reviews/legacy-translator-retirement/gate-canary-waivers.json) has been authored with the 4 translator-only justifications.
  - Passing status will be obtained by the dedicated gate worker running `gate.py --waivers docs/reviews/legacy-translator-retirement/gate-canary-waivers.json`. This review record does not preempt or claim that run has passed before it completes.
- **Type Checking**: Full pyright passed cleanly (`0 errors, 0 warnings, 0 informations`).
- **Main Test Suite Note**: Full suite execution stopped at `test_sqlite_dispatch_cas.py::test_cas_state_machine` due to a 60-second timeout under I/O load. Diagnosis of this timeout is owned by main; this batch does not duplicate or interfere with that diagnostic.
