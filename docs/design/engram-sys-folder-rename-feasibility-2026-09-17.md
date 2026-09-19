# `_sys` folder renameability — feasibility conclusion (Part C)

**Status: RATIFIED under DIR-006, 2026-09-19** (unanimous agreement: cc, and `ag.effort` standing in as one-time user-authorized proxy for `cx` -- see Addendum 4). Its central claim was already superseded on 2026-09-18 -- see the second addendum below. The user pushed back on treating the Python import-namespace coupling as an unfixable "true boundary," and a real, empirically-verified fix exists. The original research below (sections through "Conclusion") remains accurate for *why the naive approaches fail* and is kept as real history — do not delete it — but its bottom-line recommendation ("do not attempt dynamic renameability") no longer holds as stated. Read the 2026-09-18 addendum for the technical position, and Addendum 4 for the ratification record and corrected file counts. **Implementation of Part 1 (bootstrap + `root.py` + unit-test migration) may now begin**; Phase 4 (HIGH RISK core modules) remains gated per Addendum 4 §4.

## Finding: dynamic renameability is not viable with reasonable effort [SUPERSEDED — see Addendum 2]

Traced the real boot path: `engram.cmd` hardcodes `.\_sys\...` paths directly; `_sys/core/dispatch.bat` derives its own root via `%~dp0..`; `dispatcher.py` independently computes `sys_dir = Path(__file__).parent.parent.resolve()`. There is no single threaded decision point — the codebase relies on each script knowing its own position relative to a folder literally named `_sys`.

Independently verified counts (cc, direct grep against `D:\Engram&Peerhub\engram-main-worktree`):
- **31 Python files** independently re-derive their own root via `Path(__file__).resolve().parent.parent` (or the equivalent) rather than receiving it from one shared source.
- **14 Python files** use `from _sys.<module> import ...` / `import _sys.<module>` — a real Python package-namespace dependency on the literal string `_sys`, not just a path string. Renaming the folder would break these at the language level (import resolution), not just at the filesystem level.
- Batch-file entrypoints (`engram.cmd`, `_sys/core/bootstrap.bat`) hardcode `_sys\...` paths directly, with no indirection at all.

## Recommendation: do not attempt dynamic renameability [SUPERSEDED — see Addendum 2]

Making the live codebase name-agnostic would require, at minimum: (1) a discovery mechanism for pure batch files with no hardcoding, (2) rewriting 30+ Python scripts to stop deriving their own path and instead consume a shared, injected root, (3) resolving the Python import-namespace coupling (`_sys.core` etc.), which cannot be done by path/env-var tricks alone. This is a multi-week, high-risk restructure that reproduces the exact shape of the 2026-06-18 cancelled root-swap (`_sys` → `_sys_new`, abandoned after reaching 37,458 files with an incomplete `docs-v2` migration) — the same failure mode, not a coincidence: both are attempts to make a deeply path-coupled codebase agnostic to its own root folder's name.

**If renameability is ever a hard requirement, the only low-risk path is narrow, not dynamic:** a documented, tested, one-time migration tool — rename the folder on disk, literal-string-replace across every `.cmd`/`.bat`/`.py`/`.json` reference, rewrite the Python imports — leaving the live codebase hardcoded to whatever name the migration produced, exactly like a one-shot find-and-replace refactor rather than a runtime capability. This is a separate, bounded feature request with its own real risk (still touches every one of the ~44+ files), not something to build as a side effect of this round.

## Conclusion

Per explicit user permission to skip this if too hard: **closed as not worth attempting** in its dynamic form. No implementation follows from this round. A future narrow one-time-migration-tool request would need its own scoping and, per standing policy, its own dialectical review before implementation given the real risk profile.

## Addendum (2026-09-18): a smaller, distinct, worthwhile step — 2-voice converged

Does not reopen dynamic renameability. A narrower, low-risk, incremental hygiene improvement is worth doing on its own merits, verified separately from the rejected big-bang approach:

**Centralize the 31 files' own `Path(__file__).resolve().parent.parent` (or equivalent) self-location logic into one shared module** (e.g. `_sys/core/root.py`, computing it exactly once) that those files import and call instead of re-deriving it themselves. This is a pure DRY refactor — zero behavior change, each file convertible and test-verified independently, one at a time. It does **not** make `_sys` renameable: the shared module itself still lives inside a folder literally named `_sys`, and the 14 files with `from _sys.<module> import ...` still have a hard Python import-namespace dependency on that exact name — this is a language-level constraint (Python's import resolution requires a real matching package/folder name), not something a shared constant or environment variable can abstract away. Those 14 files are the genuine boundary of renameability and should be left untouched; a thin `sys.path` shim was considered and rejected (breaks static analysis, IDE tooling, and PEP 8 for no real gain). Centralizing the other 31 is still valuable independent of renaming: it shrinks the actual surface area of path-hardcoding, and is the specific prerequisite that would make a future one-time migration tool meaningfully simpler to build (updating one shared module's self-location call plus the 14 import sites, not 31+ independent call sites).

**Proposed migration order, safest first, each phase fully verified before the next:**
1. **Unit tests** (`_sys/tests/unit/test_*.py`, ~29 files) — fail loudly in CI/dev only, no production exposure.
2. **Low-risk tooling/checks** (`_sys/checks/_common.py`, `_sys/checks/check_*.py`, `tools/winget/build_*.py`, `import_legacy_manifest.py`) — standalone, outside the critical runtime path.
3. **Core non-critical modules** (`_sys/core/version_resolver.py`, `tidy_temp.py`, `migrate_ais_to_engram.py`, `dispatcher.py`). Note: `_sys/core/version.py` uses `Path(__file__).parent` (one level shallower than the rest, verified by direct read) — do not blindly apply the same `.parent.parent` substitution here; check each call site's actual depth before converting.
4. **HIGH RISK, last, extra care** (`_sys/core/provisioner.py`, `setup.py`, `updater.py`, `uninstaller.py`, `_sys/tests/unit/conftest.py`) — boot/update/uninstall-critical; a mistake here can leave an install unable to start or self-update, or break the entire test suite's fixture setup.

Not yet implemented — a proposal for cx's review (still rate-limited) alongside the rest of this backup-simplification round.

## Addendum 2 (2026-09-18): the import-namespace "true boundary" is actually fixable — recommendation reversed

**Recount first:** the "14 files" above was imprecise — it's 14 import *lines* across **9 distinct files**, and 6 of those 9 are test files (`_sys/tests/unit/test_check_unreferenced_functions.py`, `test_env_loader_json.py`, `test_launcher_log.py`, `test_layout_migration.py` ×4 lines, `test_state_paths.py` ×2 lines, `test_version_ssot.py`). Only 3 are production/tooling code: `_sys/checks/check_tool_updates.py`, `_sys/core/layout_migration.py`, `_sys/core/version_resolver.py`.

**The fix, empirically verified (cc ran a real test, not just reasoning about it):** Python's import machinery lets you register an arbitrary on-disk directory under a *fixed, stable* name in `sys.modules`, once, at process bootstrap, before anything imports it:

```python
import importlib.machinery, importlib.util, sys
from pathlib import Path

def bootstrap_root_package(actual_root: Path, stable_name: str = "_sys") -> None:
    if stable_name in sys.modules:
        return
    # _sys has no __init__.py (implicit namespace package) -- a plain
    # spec_from_file_location() would raise FileNotFoundError looking for
    # one. Build the ModuleSpec directly instead, exactly like Python's
    # own PEP 420 namespace-package machinery does internally.
    spec = importlib.machinery.ModuleSpec(stable_name, None, is_package=True)
    spec.submodule_search_locations = [str(actual_root)]
    module = importlib.util.module_from_spec(spec)
    sys.modules[stable_name] = module
```

The virtual import name (`"_sys"`) never changes, so **none of the 9 files' import statements need to be touched at all** — only the physical on-disk folder can be renamed to anything, as long as `bootstrap_root_package()` runs once, early, pointing at wherever it actually is.

**Empirically confirmed** (cc, real Python run against a throwaway fake root named `totally_not_sys` with the exact same shape as `_sys` — no `__init__.py` anywhere, a nested `core/layout_migration.py`): a 2-level deep import, `from _sys.core.layout_migration import merge_declarations_impl`, resolved correctly and loaded the physical file from `.../totally_not_sys/core/layout_migration.py`. Python's import machinery naturally walks `__path__`/`submodule_search_locations` downward through the namespace hierarchy — no per-submodule registration is needed, contrary to an initial worry.

**Why this doesn't hit the "breaks static analysis" objection** raised against a `sys.path`-manipulation shim in the original research: pyright/type-checking only ever runs against the real git checkout during development and CI, where the folder is always literally `_sys` — it never runs against an end-user's renamed portable installation. Static analysis and the runtime bootstrap are decoupled by *when* each one executes, not by any typing trick.

**Where it must run** (verified against the real entrypoint chain): at the top of `_sys/core/dispatcher.py`, before `_resolve_paths` imports `core.env_loader` and before its `importlib.import_module()` calls — this covers everything routed through `engram.cmd`/`dispatch.bat`. Also at the top of `_sys/checks/check_tool_updates.py`, since it has its own `if __name__ == "__main__":` entrypoint and can run standalone. `_sys/tests/unit/conftest.py`'s existing `sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))` (its own comment: "so 'from _sys.core import ...' works") can be replaced by the same `bootstrap_root_package()` call — verified this is the real, current mechanism the 6 test files rely on, and pytest imports test files by path (no `__init__.py` under `_sys/tests/`), so they cleanly pick up whatever `sys.modules["_sys"]` the conftest bootstrap registered.

**Real remaining cost, unchanged from Addendum 1:** `tools/winget/build_package.py` still hardcodes `_sys` literally (`repo_root / "_sys"`, `"_sys/runtimes.json"`, `"_sys/core/release-manifest.json"` — verified at its real line numbers) — this only affects a *developer* building a release from a renamed checkout, never an end user's already-downloaded/renamed installation, so it's real but non-blocking, and falls under the same "31-files-plus-batch-files" de-hardcoding bucket as Addendum 1, not a new category.

**Revised recommendation: dynamic renameability is viable**, as a two-part effort: (1) the import-namespace bootstrap above (small, now-verified, ~9 files affected but 0 of them need editing — only the bootstrap call sites, ~2-3 locations, need adding), (2) the Addendum 1 path-centralization work (31 files, phased, safest-first) plus the batch-file entrypoints and `build_package.py`. Neither part alone reproduces the 2026-06-18 failure's shape (that attempt tried to move/duplicate the entire tree at once with no bootstrap indirection at all); done as two small, independently-verified, incremental steps, this is a fundamentally different risk profile than the cancelled root-swap. Still not implemented — this, like everything else in this round, is pending cx's independent review.

## Addendum 3 (2026-09-18): the developer-facing (packaging) angle, scoped precisely

User asked to review the "developer-side" packaging concern (flagged but not detailed in Addendum 2) properly rather than leaving it vague. Direct source reading of `tools/winget/build_package.py` and `tools/winget/import_legacy_manifest.py` (cc):

**Two genuinely different kinds of `_sys` string in these files — only one kind needs fixing:**

1. **Real source-checkout references** — these break if a *developer* renames their local checkout's `_sys` folder:
   - `build_package.py` line 158: `sys_dir = repo_root / "_sys"`.
   - `import_legacy_manifest.py` line 28: `manifests_dir = repo_root / "_sys" / "core" / "release-manifests"`.
   - Both files *also* independently re-derive their own `repo_root` (`build_package.py` line 42: `Path(__file__).resolve().parent.parent.parent`; line 515: `script_dir.parent.parent`) — meaning both already belong in Addendum 1's "31 files" bucket for that part, and additionally need their `_sys`-literal replaced with a value sourced from the same shared root-finder module (or a `--sys-dir-name` override, defaulting to `"_sys"`).

2. **Output-package archive-path strings — NOT a bug, do not "fix" these:** `build_package.py` lines 196-249 (`"_sys/runtimes.json"`, `"_sys/core/release-manifest.json"`, etc.) and `import_legacy_manifest.py` lines 101/104 (checking `item.filename == "_sys/runtimes.json"` when reading a package back) are the *shipped package's own internal folder-name convention* — what the zip's internal paths are called has no required relationship to what a developer happens to name their local checkout. These can stay as literal `"_sys/..."` strings indefinitely: the Addendum 2 runtime bootstrap already means an end user's *installed* copy is renameable regardless of what name the package used internally at build time. Renaming these too would be pure scope creep with no corresponding requirement — the two kinds of string look identical in a grep but answer different questions ("where does *my checkout* keep `_sys`" vs. "what do *shipped packages* call their `_sys` folder internally"), and only the first needs to change.

**Net effect on scope:** no new files beyond what Addendum 1's Phase 2 ("low-risk tooling," which already named `tools/winget/build_*.py`) already covered — this addendum just makes precise which specific lines in those files are real fixes versus which look like matches but are a different, unrelated convention that should be left alone. Still pending cx's review alongside the rest of this round.

## Addendum 4 (2026-09-19): DIR-006 Ratification Review (cx substitute vote by ag.effort)

Formal review ratified with amendment under DIR-006. cx remained real-vendor-rate-limited; per explicit user instruction, `ag.effort` rendered the third vote as a one-time named exception for this decision only (not a standing policy change). An earlier attempt at this same task landed on `ag.deepthink` (a hub tier-auto-selection artifact) and searched the wrong repo root (`P:\_sys`, an unrelated legacy AI-collaboration-hub codebase), producing a false REJECT built on non-existent-file claims — that verdict was discarded after cc independently confirmed every "missing" file actually exists under the correct root. This redispatch explicitly pinned `ag.effort` and the correct repo root (`D:\Engram&Peerhub\engram-main-worktree`); cc independently re-verified every citation below against the real files before accepting this verdict.

### 1. Empirical verification & technical concurrence
- Addendum 2's `ModuleSpec(stable_name, None, is_package=True)` + `submodule_search_locations = [str(actual_root)]` mechanism was independently re-confirmed via empirical execution against both a synthetic tree and a full renamed copy of the real `_sys` codebase (including running `layout_migration._merge_component_maps(...)` and importing `version_resolver` under the renamed root). Downstream submodule imports and method calls executed cleanly without `actual_root` on `sys.path`.
- Addendum 3's distinction between source-checkout paths (real fixes) and archive-convention paths (immutable packaging format) is confirmed correct by direct inspection of the current file content.

### 2. Required citation & metric amendments (all independently re-verified by cc)
- **Import statements (Addendum 2 recount correction):** the actual codebase contains **24 import lines across 12 distinct files** (not 14 across 9) -- confirmed via direct grep against `D:\Engram&Peerhub\engram-main-worktree`.
  - *Test files (7):* `test_check_unreferenced_functions.py`, `test_dispatch_wiring.py` (line 202, missed in Addendum 2), `test_env_loader_json.py`, `test_launcher_log.py` (uses `import _sys.core.launcher as launcher`, a different import style than the rest), `test_layout_migration.py` (5 lines), `test_state_paths.py` (3 lines), `test_version_ssot.py` (2 lines).
  - *Prod/tooling files (5):* `_sys/checks/check_tool_updates.py`, `_sys/core/layout_migration.py`, `_sys/core/version_resolver.py`, plus `_sys/checks/check_encoding.py:41` (`from _sys.core import provisioner`, missed in Addendum 2) and `tools/winget/build_package.py:53-54`.
- **Self-location logic count (Main Body & Addendum 1):** the codebase contains **65 distinct Python files** (74 occurrences) performing `Path(__file__)` self-location logic, not 31 -- 43 unit test files, 9 core modules, 7 checks modules, 3 winget tooling scripts, 3 other test files.
- **Bootstrap call sites:** add `_sys/checks/check_encoding.py` (has its own `if __name__ == "__main__":` block and imports `_sys.core.provisioner` at line 41) to the bootstrap-injection list alongside `_sys/core/dispatcher.py`, `_sys/checks/check_tool_updates.py`, and `_sys/tests/unit/conftest.py`.
- **Packaging line-number drift (Addendum 3), confirmed against current file content:**
  - `tools/winget/build_package.py`: line 166 (was cited as 158); line 50 (was cited as 42); lines 522-523 (was cited as 515).
  - `tools/winget/import_legacy_manifest.py`: line 31 (was cited as 28); lines 104/107 (was cited as 101/104).

### 3. Migration-order qualification
Addendum 1's phased order is otherwise sound, with one qualification: `conftest.py` was grouped into Phase 4 (HIGH RISK), but since Addendum 2 already established `conftest.py` needs `bootstrap_root_package()`, that specific change should land in **Phase 1**, not Phase 4, so all subsequent test migrations run under the bootstrap harness from the start.

### 4. Implementation scope & authorization
Ratification authorizes starting now: `bootstrap_root_package()` in `_sys/core/dispatcher.py`, `_sys/checks/check_tool_updates.py`, `_sys/checks/check_encoding.py`, and `_sys/tests/unit/conftest.py` (Phase 1); creation of `_sys/core/root.py`; and Phase 1 unit-test migration. **Gated before Phase 4** (HIGH RISK: `provisioner.py`, `setup.py`, `updater.py`, `uninstaller.py`): a full green `pytest` pass across Phases 1-3, and a concrete design note for batch-entrypoint discovery (`engram.cmd` and `_sys/core/bootstrap.bat` hardcode `_sys` at 15+ call sites; making the Python runtime renameable does not by itself make the Windows CLI entrypoint renameable -- an `ENGRAM_SYS_DIR`-style env-var pass-through or equivalent must be designed before Phase 4).
